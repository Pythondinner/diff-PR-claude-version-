from _common import append_event, read_stdin_json


def main():
    payload = read_stdin_json()
    append_event("post_tool_use", payload)


if __name__ == "__main__":
    main()
