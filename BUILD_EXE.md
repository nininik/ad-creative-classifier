# .exe로 패키징하기 (Windows)

다른 사람에게 Python 설치 없이 더블클릭만으로 실행 가능한 프로그램을 보내고 싶을 때
사용하는 방법입니다.

## ⚠️ 꼭 알아두실 점

- **.exe는 반드시 Windows 컴퓨터에서 빌드해야 합니다.** (Mac/Linux에서 빌드한 실행 파일은
  Windows에서 동작하지 않고, 반대로 Windows에서 만든 .exe만 다른 Windows PC에서 동작합니다.)
- 완성된 exe 파일 용량은 **200~400MB 정도**로 꽤 큽니다. opencv, scikit-image 같은
  이미지 처리 라이브러리를 통째로 안에 담기 때문이에요. 이메일 첨부보다는
  구글드라이브, 원드라이브 같은 파일 공유 링크로 전달하는 걸 추천드려요.
- 일부 백신 프로그램이 PyInstaller로 만든 exe를 (내용과 무관하게) 의심 파일로
  오탐하는 경우가 있어요. 받는 분이 백신 경고를 보시면, 신뢰할 수 있는 출처에서
  받은 파일이라고 알려주시면 됩니다.

## 빌드 방법

### 1. Windows 컴퓨터에 Python 설치
[python.org](https://www.python.org/downloads/) 에서 Python 3.10~3.12 버전을 설치하세요.
설치 시 **"Add Python to PATH"** 체크박스를 꼭 체크하세요.

### 2. 이 폴더에서 빌드 스크립트 실행
`build.bat` 파일을 더블클릭하면 자동으로:
1. 필요한 패키지 설치
2. PyInstaller로 exe 빌드
3. `dist\AdCreativeClassifier.exe` 생성

까지 한 번에 진행됩니다. 처음 실행 시 몇 분 정도 걸릴 수 있어요.

### 3. 완성된 exe 확인
빌드가 끝나면 `dist` 폴더 안에 `AdCreativeClassifier.exe` 파일이 생깁니다.
이 파일 하나를 더블클릭하면:
- 자동으로 로컬 서버가 실행되고
- 기본 브라우저가 자동으로 열리면서
- 바로 사용할 수 있는 화면이 뜹니다.

이 exe 파일 **하나만** 다른 사람에게 전달하면 됩니다 (Python 설치 불필요).

## 다른 사람이 exe를 받았을 때 사용법
1. `AdCreativeClassifier.exe` 더블클릭
2. 까만 콘솔 창이 하나 뜨면서 잠시 후 브라우저가 자동으로 열림
   (자동으로 안 열리면 콘솔에 나온 주소 `http://127.0.0.1:5000` 을 브라우저에 직접 입력)
3. 평소처럼 사용
4. 다 쓰면 콘솔 창을 닫거나 Ctrl+C로 종료

## 만약 빌드 중 오류가 나면

PyInstaller가 opencv/scikit-image/pymupdf 안의 일부 모듈을 자동으로 못 찾아서
"ModuleNotFoundError" 같은 오류가 날 수 있어요. 이럴 땐 `build.bat` 안의 pyinstaller
명령어에 아래처럼 한 줄씩 추가해서 다시 시도해보세요.

```
--hidden-import=모듈이름
```

예를 들어 `cv2` 관련 오류가 나면 `--collect-all opencv-python-headless` 를,
`No module named 'skimage.xxx'` 오류가 나면 `--hidden-import=skimage.xxx` 를 추가하는 식입니다.

## ⚠️ CLIP(torch)을 exe에 포함하고 싶다면

`build.bat`은 기본적으로 CLIP 없이(phash/color/structure/부분포함만) 빌드하도록
되어 있어요. **CLIP까지 exe 안에 넣으려면 각오하셔야 할 게 있어요:**

- torch가 워낙 커서, exe 용량이 **1.5GB~2.5GB**까지 커질 수 있어요.
- PyInstaller가 torch를 onefile로 묶는 과정에서 자주 실패하거나 오래 걸려요.
  (torch는 내부적으로 수백 개의 동적 라이브러리를 런타임에 불러오는 구조라서,
  PyInstaller가 전부 자동으로 못 찾는 경우가 흔해요.)
- 이런 이유로 CLIP을 포함해서 배포하고 싶으시면 `--onefile` 대신
  **`--onedir`**로 빌드하는 걸 강하게 추천드려요 (아래 참고).

CLIP까지 포함해서 빌드하려면 `build.bat`의 pyinstaller 명령어에 아래 옵션을 추가하세요:

```
--collect-all torch --collect-all open_clip
```

그래도 오류가 나면, 에러 메시지에 나온 모듈 이름을 `--hidden-import=` 뒤에
붙여서 하나씩 추가해가며 재시도하는 수밖에 없어요 (torch 특성상 흔한 일이에요).

**더 현실적인 대안**: CLIP 기능은 굳이 exe에 안 넣고, "일반 파이썬 스크립트/웹앱으로
쓸 때만" 쓰는 고급 옵션으로 남겨두는 것도 좋은 방법이에요. exe는 가볍고 빠르게
유지하고, CLIP이 필요한 정밀 비교는 필요할 때만 파이썬으로 직접 돌리는 식으로요.

## 더 가볍게/빠르게 만들고 싶다면 (onedir 방식)

`--onefile` 대신 `--onedir`로 빌드하면, exe 파일 하나 대신 폴더 하나가 생기는데
(그 폴더 안에 exe + 필요한 파일들이 풀려있는 상태) **실행 속도가 더 빠르고**
백신 오탐 가능성도 더 낮습니다. 대신 배포할 때 폴더 전체를 zip으로 묶어서
보내야 해요.

`build.bat` 안의 `--onefile` 을 `--onedir` 로 바꾸기만 하면 됩니다.
