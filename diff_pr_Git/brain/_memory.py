import uuid
from datetime import datetime, timezone

DORMANT_AFTER_ROUNDS = 5
PROPOSE_THRESHOLD = 2


def active_confirmed_patterns(memory_list, current_round):
    """已确认、且没有沉寂太久的模式——是否沉寂靠"当前轮次 - 最后出现轮次"实时算，
    不持久化状态，省掉一次额外的后台更新步骤。"""
    return [
        m for m in memory_list
        if m["status"] == "confirmed"
        and current_round - m["last_seen_round"] <= DORMANT_AFTER_ROUNDS
    ]


def candidate_patterns(memory_list):
    return [m for m in memory_list if m["status"] == "candidate"]


def format_known_patterns(memory_list, current_round):
    """组装成喂给 DeepSeek 的"已知模式"上下文。已确认和候选的都给，
    这样候选模式才有机会被匹配、累加出现次数，够 2 次才提议给用户确认。"""
    confirmed = active_confirmed_patterns(memory_list, current_round)
    candidates = candidate_patterns(memory_list)
    lines = []
    for m in confirmed:
        lines.append(f"- id: {m['id']} | 模式: {m['pattern']} | 状态: 已确认")
    for m in candidates:
        lines.append(f"- id: {m['id']} | 模式: {m['pattern']} | 状态: 候选（已出现 {m['occurrences']} 次）")
    if not lines:
        return "（还没有任何历史模式记录）"
    return "\n".join(lines)


def new_memory_entry(pattern, round_number):
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": uuid.uuid4().hex[:8],
        "pattern": pattern,
        "status": "candidate",
        "occurrences": 1,
        "rounds": [round_number],
        "first_seen_round": round_number,
        "last_seen_round": round_number,
        "created_at": now,
        "updated_at": now,
    }


def apply_matches(memory_list, matched_pattern_ids, new_candidate_pattern, round_number):
    """原地更新 memory_list：命中的模式累加出现次数和轮次；有新候选就新建一条。
    返回这次新跨过"提议门槛"（候选且出现 >= 2 次）的模式列表，交给上层去问用户要不要确认。"""
    by_id = {m["id"]: m for m in memory_list}
    newly_eligible = []

    for pid in matched_pattern_ids or []:
        m = by_id.get(pid)
        if not m or round_number in m["rounds"]:
            continue
        m["occurrences"] += 1
        m["rounds"].append(round_number)
        m["last_seen_round"] = round_number
        m["updated_at"] = datetime.now(timezone.utc).isoformat()
        if m["status"] == "candidate" and m["occurrences"] >= PROPOSE_THRESHOLD:
            newly_eligible.append(m)

    if new_candidate_pattern:
        memory_list.append(new_memory_entry(new_candidate_pattern, round_number))

    return newly_eligible
