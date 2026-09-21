# 책갈피 툴 – 두쪽 나누기

[English](README.en.md) · [中文](README.zh-CN.md) · [日本語](README.ja.md)

책을 펼쳐서 스캔해 한 장에 두 쪽이 담긴 PDF를 왼쪽·오른쪽 쪽으로 나눠, 이북리더기에서 한 쪽씩 넘겨 볼 수 있게 만듭니다. 이미지를 다시 압축하지 않아 화질이 그대로이고, 파일 크기도 거의 늘지 않습니다. 서버 없음, 설치 없음, 원본 무수정.

![전후 비교](docs/before_after.png)

## 다운로드

- **실행 파일**: [Releases](https://github.com/microhan1/scan-pdf-split/releases)에서 `scan-pdf-split.exe`를 받아 더블클릭. 설치 없이 바로 실행됩니다.
- **소스 실행**:

```bash
pip install -r requirements.txt
python main.py
```

## 사용법

1. PDF 파일이나 폴더를 창에 끌어다 놓습니다.
2. 미리보기를 보며 옵션을 조정합니다 (읽기 방향 · 나눌 쪽 · 분할선 자동 맞춤 · 가운데 겹침). 쪽을 넘기며 빨간 분할선을 끌거나, 쪽마다 나누기/통째로/빼기를 고를 수 있습니다.
3. **실행**을 누르면 원본 옆에 `<원본명>_split.pdf`가 만들어집니다.

- 세로로 긴 표지는 나누지 않고 그대로 둡니다.
- **자동 맞춤**은 쪽마다 제본선의 그림자나 가운데 빈 여백을 찾아 분할선을 옮깁니다. 찾지 못한 쪽은 분할선 위치 옵션대로 나눕니다.
- 회전 정보가 있는 쪽, 링크와 메모, 책갈피(목차)도 나눈 쪽에 맞춰 옮겨집니다.

명령줄로도 쓸 수 있습니다.

```bash
python main.py book.pdf
python main.py scans/ --direction rtl --overlap 1
python main.py book.pdf --no-auto --position 52
```

`python main.py --help`가 OS 언어(한국어 · English · 中文 · 日本語)로 옵션을 보여줍니다.

## 하지 않는 것

- OCR은 하지 않습니다. 글자 검색이 되는 PDF는 나눈 뒤에도 검색되지만, 옆 쪽 글자가 함께 걸릴 수 있습니다.
- 여백 자르기, 화질 보정은 별도 툴입니다. 이 툴은 쪽 나누기만 합니다.
- 잘라 낸 절반은 보이지 않을 뿐 파일 안에 남습니다. 내용을 가리는 용도로 쓰지 마세요.
- 기울어진 스캔을 바로 세우거나, 회전 정보 없이 옆으로 누운 이미지를 돌리지 않습니다.

## 시리즈

- 책갈피 툴: [스캔 PDF 보정](https://github.com/microhan1/scan-pdf-cleanup) · [여백 자르기](https://github.com/microhan1/scan-pdf-crop)
- 설치 없이 브라우저에서 쓰는 웹판: [펼침면 나누기](https://microhan1.github.io/spread-split/)
- [책갈피 라이브러리](https://github.com/microhan1/chaekgalpi)

## 라이선스

MIT. [LICENSE](LICENSE) 참조.
