"""Application entry point for OpenFish assistant."""

from src.app import create_app


def main() -> None:
    app = create_app()
    app.run()


if __name__ == "__main__":
    main()
