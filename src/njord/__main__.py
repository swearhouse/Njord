from waitress import serve

from njord.app import create_app


def main() -> None:
    serve(create_app(), host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
