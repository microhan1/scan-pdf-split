# Chaekgalpi Tools – Two-page Split

[한국어](README.md) · [中文](README.zh-CN.md) · [日本語](README.ja.md)

Splits a PDF scanned two pages per sheet (a book scanned open) into left and right pages, so it can be read one page at a time on an e-reader. Images are never re-compressed: quality stays the same and the file barely grows. No server, no install, the original file is never modified.

![Before and after](docs/before_after.png)

## Download

- **Executable**: grab `scan-pdf-split.exe` from [Releases](https://github.com/microhan1/scan-pdf-split/releases) and double-click it. Nothing to install.
- **Run from source**:

```bash
pip install -r requirements.txt
python main.py
```

## Usage

1. Drop PDF files or a folder onto the window.
2. Adjust the options while watching the preview (reading order · pages to split · automatic gutter fitting · center overlap). Step through the pages to drag the red split line, or to set a page to Split, Whole or Skip.
3. Press **Run**. `<name>_split.pdf` is written next to the original.

- Portrait covers are kept whole.
- **Gutter fitting** finds the shadow or the blank margin in the middle of each page and moves the split line there. Pages where nothing is found are split at the split-line setting.
- Rotated pages, links and notes, and bookmarks (the table of contents) follow the split pages.

There is a command line too.

```bash
python main.py book.pdf
python main.py scans/ --direction rtl --overlap 1
python main.py book.pdf --no-auto --position 52
```

`python main.py --help` prints the options in your OS language (한국어 · English · 中文 · 日本語).

## What it does not do

- No OCR. A searchable PDF stays searchable, but a search may also hit words from the neighbouring page.
- Margin cropping and image cleanup are separate tools. This one only splits pages.
- The cut-off half is hidden, not removed; it is still inside the file. Do not use this to hide content.
- It does not straighten tilted scans or turn images stored sideways without a rotation flag.

## Series

- Chaekgalpi Tools: [Scan PDF Cleanup](https://github.com/microhan1/scan-pdf-cleanup) · [Margin Crop](https://github.com/microhan1/scan-pdf-crop)
- Browser version, nothing to install: [Spread Split](https://microhan1.github.io/spread-split/)
- [Chaekgalpi Library](https://github.com/microhan1/chaekgalpi)

## License

MIT. See [LICENSE](LICENSE).
