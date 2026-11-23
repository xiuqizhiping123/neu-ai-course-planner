"""Convert a PDF to UTF-8 text using its text layer or Tesseract OCR."""

import argparse
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

import pytesseract
from pypdf import PdfReader
from pypdfium2 import PdfDocument, PdfiumError
from pydantic_settings import BaseSettings, SettingsConfigDict

OCR_TIMEOUT_SECONDS = 60


class OCRSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[1] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    tesseract_executable: Path | None = None


def extract_text(pdf_path: Path) -> list[str]:
    with pdf_path.open("rb") as pdf_file:
        reader = PdfReader(pdf_file)
        return [page.extract_text().strip() for page in reader.pages]


def extract_with_ocr(pdf_path: Path, lang: str | None) -> list[str]:
    executable = OCRSettings().tesseract_executable
    if executable is not None:
        pytesseract.pytesseract.tesseract_cmd = str(executable)

    pages = []
    with PdfDocument(pdf_path) as document, TemporaryDirectory() as temp_dir:
        image_path = Path(temp_dir) / "page.png"
        for page in document:
            with (
                closing(page),
                closing(page.render(scale=4)) as bitmap,
                bitmap.to_pil() as image,
            ):
                image.save(image_path)
            text = pytesseract.image_to_string(
                str(image_path), lang=lang, timeout=OCR_TIMEOUT_SECONDS
            )
            pages.append(text.strip())
    return pages


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, help="Input PDF file")
    parser.add_argument(
        "-o", "--output", type=Path, help="Output path (default: replace .pdf with .txt)"
    )
    parser.add_argument("--ocr", action="store_true", help="Run Tesseract on every page")
    parser.add_argument("--lang", help="Tesseract language code, e.g. eng+chi_sim")
    args = parser.parse_args()

    if args.lang is not None and not args.ocr:
        parser.error("--lang requires --ocr")

    pdf_path = args.pdf
    text_path = args.output or pdf_path.with_suffix(".txt")
    if not pdf_path.is_file():
        parser.error(f"Input PDF not found: {pdf_path}")
    if text_path.exists() and text_path.samefile(pdf_path):
        parser.error("The input PDF and output text file must be different")

    try:
        pages = (
            extract_with_ocr(pdf_path, args.lang) if args.ocr else extract_text(pdf_path)
        )
    except pytesseract.TesseractNotFoundError as error:
        parser.exit(1, f"{parser.prog}: {error}. Set TESSERACT_EXECUTABLE or PATH.\n")
    except PdfiumError as error:
        parser.exit(1, f"{parser.prog}: {error}\n")

    text_path.write_text("\n\n".join(pages) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
