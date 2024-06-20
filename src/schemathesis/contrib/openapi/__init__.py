from . import fill_missing_examples, formats


def install() -> None:
    formats.install()
    fill_missing_examples.install()


def uninstall() -> None:
    formats.uninstall()
    fill_missing_examples.uninstall()
