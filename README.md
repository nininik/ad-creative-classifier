# 광고소재 유사도 분류기 (Ad Creative Classifier)

기준 소재(A)와 비교 대상 소재(B, C, D...)를 비교해서 **동일 / 유사 / 다름**으로
자동 분류하고, 그 결과에 따라 파일명을 바꿔주는 로컬 전용 도구입니다.
브라우저 웹앱과 CLI 두 가지 방식으로 쓸 수 있고, 인터넷 연결 없이도 동작합니다.

## 주요 기능

- 이미지(jpg/png/bmp/webp)와 PDF(첫 페이지 기준) 소재 비교 지원
- 다섯 가지 지표를 조합해 판단
  - **phash**: 리사이즈·압축 정도만 다른 근접 동일 소재 탐지
  - **color**: 색상 히스토그램 기반 색감 유사도
  - **structure**: SSIM 기반 구도/레이아웃 유사도
  - **부분 포함 탐지**: A가 B의 일부 영역에 리사이즈되어 재사용된 경우까지 탐지 (멀티스케일 템플릿 매칭)
  - **CLIP (선택이지만 추천)**: 색상·구도가 아니라 이미지에 담긴 실제 내용/의미까지 이해해서 비교해 정확도를 크게 높여주는 오픈소스 AI 모델
- 공통 템플릿 영역(로고, 상단 배지 등)을 제외하고 비교하는 crop 옵션
- 결과 파일명 자동 변경 (`[동일]_0.99_원본이름.jpg` 형식) + CSV 로그
- 브라우저 기반 웹앱: 파일 첨부만으로 사용, 결과 화면에서 파일명 직접 수정 가능
- Windows용 단일 실행 파일(.exe)로 패키징 가능 (Python 설치 없이 배포)
- 100% 로컬 처리 — 이미지가 외부 서버로 전송되지 않음

---

## 빠른 시작 — Python 없이 바로 쓰기 (Windows, exe)

Python을 설치하고 싶지 않다면 이 방법이 제일 간단합니다.

1. [Releases](../../releases) 페이지에서 최신 `AdCreativeClassifier.exe` 다운로드
2. 더블클릭 실행 (Windows가 "PC 보호" 경고를 띄우면 **추가 정보 → 실행** 클릭)
3. 잠시 후 브라우저가 자동으로 열리면서 바로 사용 가능

단, exe 버전에는 CLIP이 포함되어 있지 않습니다 (용량 문제). CLIP까지 쓰고 싶거나 Mac/Linux에서
쓰고 싶다면 아래 "소스코드로 실행하기"를 따라 하세요.

---

## 소스코드로 실행하기

### 1. Python 설치 (이미 설치되어 있다면 건너뛰기)

터미널(Windows는 cmd 또는 PowerShell)에서 아래 명령어를 입력해서 이미 설치되어 있는지 먼저 확인하세요.

```bash
python --version
```

`Python 3.x.x` 같은 버전이 나오면 이미 설치된 것이니 다음 단계로 넘어가면 됩니다.
"명령어를 찾을 수 없다"는 메시지가 나오면 아래 순서로 설치하세요.

1. [python.org/downloads](https://www.python.org/downloads/) 접속 → 최신 버전(3.10 이상) 다운로드
2. 설치 시작 화면에서 **"Add Python to PATH"** 체크박스를 반드시 체크 (이걸 빠뜨리면 터미널에서 `python` 명령어가 안 먹힙니다)
3. 설치 완료 후 터미널을 새로 열고 `python --version`으로 다시 확인

### 2. 이 저장소 받기

**git이 설치되어 있다면:**
```bash
git clone https://github.com/사용자명/저장소이름.git
cd 저장소이름
```

**git이 없다면:** 저장소 페이지 상단의 초록색 **Code → Download ZIP** 버튼으로 받아서 압축을 풀고,
그 폴더로 이동하면 됩니다.

### 3. 필요한 패키지 설치

```bash
pip install -r requirements.txt
```

### 4. (선택 사항, 정확도 크게 향상) CLIP 설치

CLIP까지 쓰면 색상·구도가 아니라 이미지에 담긴 실제 내용까지 이해해서 비교하기 때문에
정확도가 크게 올라갑니다. 설치 안 해도 나머지 기능은 정상 동작하고 CLIP 옵션만 자동으로 꺼집니다.

**⚠️ 반드시 아래 순서 그대로 설치하세요.** 순서를 지키면 약 550MB, 순서를 건너뛰면
GPU(CUDA) 라이브러리까지 딸려와서 **4.5GB 이상**을 차지할 수 있습니다 (이 도구는 GPU가
전혀 필요 없습니다 - CPU로 충분히 빠릅니다).

```bash
# 1) CPU 전용 torch 먼저 설치 (약 200MB)
pip install torch --index-url https://download.pytorch.org/whl/cpu

# 2) 그 다음 open_clip_torch 설치 (위에서 torch를 먼저 깔아뒀으므로 GPU 버전으로 덮어쓰지 않음)
pip install -r requirements-clip.txt
```

순서를 건너뛰고 `pip install torch`를 먼저 실행해버렸다면, `pip uninstall torch` 후
위 순서대로 다시 설치하시면 됩니다.

---

## 사용법 1: 로컬 웹앱 (대부분의 사용자에게 추천)

```bash
python app.py
```

실행 후 브라우저에서 `http://127.0.0.1:5000` 접속:

1. **기준 소재(A)** 업로드 — 비교의 기준이 될 파일 1개
2. **비교 대상 소재(B, C, D...)** 업로드 — 여러 개 한 번에 선택 가능
3. (선택) **고급 설정**에서 가중치·임계값·crop·CLIP 사용 여부 조정
4. **분류 시작하기** 클릭
5. 결과 화면에서 분류/점수/미리보기 확인 (이미지 클릭하면 크게 볼 수 있음), 필요하면 ✏️ 버튼으로 파일명 직접 수정
6. **결과 ZIP 다운로드**

## 사용법 2: CLI

여러 폴더를 스크립트로 자동화하거나 반복 실행하고 싶은 분들을 위한 명령줄 방식입니다.

```bash
python ad_creative_classifier.py --reference A.jpg --targets B.jpg C.jpg D.jpg
```

폴더째로 비교:

```bash
python ad_creative_classifier.py --reference A.jpg --targets ./targets_folder
```

실제로 파일명을 바꾸기 전에 미리 확인하려면:

```bash
python ad_creative_classifier.py --reference A.jpg --targets ./targets_folder --dry-run
```

자주 쓰는 옵션:

| 옵션 | 설명 |
|---|---|
| `--dry-run` | 실제로 파일명을 바꾸지 않고 결과만 미리 확인 |
| `--weights PHASH COLOR STRUCTURE CLIP` | 지표별 가중치 (기본값: `0.15 0.15 0.25 0.45`) |
| `--no-clip` | CLIP 계산 끄기 |
| `--no-containment` | 부분 포함 탐지 끄기 |
| `--crop-top / --crop-bottom / --crop-left / --crop-right` | 공통 템플릿 영역 제외 비율(%) |
| `--identical-threshold / --similar-threshold` | 동일/유사 판정 기준 점수 |
| `--pdf-dpi` | PDF를 이미지로 렌더링할 해상도 |

전체 옵션은 `python ad_creative_classifier.py --help` 로 확인할 수 있습니다.

---

## Windows용 .exe로 직접 빌드하기

이미 만들어진 exe는 위 "빠른 시작"에서 받을 수 있고, 직접 빌드하고 싶다면 `BUILD_EXE.md`를 참고하세요.
`build.bat` 한 번 실행으로 `dist/AdCreativeClassifier.exe`가 만들어집니다.

이 저장소는 GitHub Actions로 태그를 푸시하면 자동으로 exe를 빌드해서
[Releases](../../releases)에 올려주는 워크플로우도 포함하고 있습니다
(`.github/workflows/build-exe.yml`). CLIP은 exe 용량/빌드 안정성 문제로
기본 빌드에서는 제외되어 있습니다 — 필요하면 소스에서 직접 설치해서 쓰세요.

## 참고 사항

- 웹앱은 `127.0.0.1`(내 컴퓨터)에서만 접속되도록 되어 있어 다른 사람이 접근할 수 없습니다.
  외부에 공개하는 용도로 쓰지 마세요.
- 업로드된 파일은 시스템 임시 폴더에서 처리되며, 원본 폴더의 파일을 직접 바꾸지 않습니다.
  결과는 항상 ZIP으로 새로 받는 방식입니다.
- 서버를 끄면(Ctrl+C) 임시 작업 파일이 시스템 임시 폴더에 남아있을 수 있으니 필요 없으면 정리하세요.

## 라이선스

MIT License. `LICENSE` 파일을 참고하세요.
