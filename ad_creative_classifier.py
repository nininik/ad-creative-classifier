#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ad_creative_classifier.py

기준 소재(A)와 비교 대상 소재(B, C, D, ...)를 비교하여
'동일' / '유사' / '다름' 으로 분류하고, 그 결과에 따라 파일명을 변경합니다.

비교는 디자인/구도/색감 유사도에 초점을 맞추며, 텍스트나 일부 요소가 달라도
전체적인 레이아웃과 색감이 비슷하면 '유사'로 판정합니다.

사용법:
    python ad_creative_classifier.py --reference A.jpg --targets ./targets_folder
    python ad_creative_classifier.py --reference A.jpg --targets B.jpg C.jpg D.jpg
    python ad_creative_classifier.py --reference A.jpg --targets ./targets_folder --dry-run

이미지(jpg/png/bmp/webp) 외에 PDF 소재도 지원합니다. PDF는 첫 페이지를 이미지로
렌더링한 뒤 동일한 방식으로 비교합니다 (여러 페이지 PDF는 1페이지만 사용).

필요 라이브러리 설치 (기본):
    pip install pillow imagehash opencv-python-headless scikit-image numpy pymupdf

CLIP(내용 기반 의미 유사도) 기능까지 쓰려면 추가로:
    pip install torch open_clip_torch
(용량이 큽니다 - 수백MB~1GB. 설치 안 해도 나머지 기능은 그대로 동작하고,
 CLIP 관련 옵션만 자동으로 꺼집니다.)
"""

import argparse
import csv
import io
import sys
from pathlib import Path

import cv2
import pymupdf as fitz  # PDF 렌더링용 (구버전 호환을 위해 fitz로 alias)
import imagehash
import numpy as np
from PIL import Image
from skimage.metrics import structural_similarity as ssim

# CLIP은 무거운 선택적 의존성이라, 없어도 나머지 기능이 정상 동작하도록
# 예외 처리로 감싸서 불러옵니다.
try:
    import torch
    import open_clip
    CLIP_AVAILABLE = True
except ImportError:
    CLIP_AVAILABLE = False

_CLIP_STATE = {"model": None, "preprocess": None}
_clip_warned = False


def _warn_clip_unavailable():
    global _clip_warned
    if not _clip_warned:
        print("[안내] CLIP 관련 라이브러리(torch, open_clip_torch)가 설치되어 있지 않아 "
              "CLIP 유사도 없이 계산합니다.")
        print("       pip install torch open_clip_torch 로 설치하면 이미지 '내용' 기반 "
              "비교를 추가로 사용할 수 있습니다.")
        _clip_warned = True


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
PDF_EXTENSIONS = {".pdf"}
SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | PDF_EXTENSIONS


def load_pdf_first_page_as_pil(path: Path, dpi: int = 200) -> Image.Image:
    """PDF의 첫 페이지를 지정한 DPI로 렌더링하여 PIL 이미지로 반환."""
    doc = fitz.open(str(path))
    try:
        if doc.page_count == 0:
            raise ValueError(f"PDF에 페이지가 없습니다: {path}")
        page = doc.load_page(0)
        zoom = dpi / 72  # PDF 기본 72dpi 기준 배율
        matrix = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        img_bytes = pix.tobytes("png")
        return Image.open(io.BytesIO(img_bytes)).convert("RGB")
    finally:
        doc.close()


def load_creative(path: Path, pdf_dpi: int = 200):
    """
    이미지 또는 PDF 파일을 열어 (PIL 이미지, OpenCV 이미지) 튜플로 반환.
    PDF는 첫 페이지만 렌더링해서 사용합니다.
    """
    suffix = path.suffix.lower()
    if suffix in PDF_EXTENSIONS:
        pil_img = load_pdf_first_page_as_pil(path, dpi=pdf_dpi)
    else:
        pil_img = Image.open(path).convert("RGB")

    cv_img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    return pil_img, cv_img


def crop_image(pil_img: Image.Image, crop):
    """
    상/하/좌/우를 비율(0~1)만큼 잘라낸 새 PIL 이미지를 반환.
    로고, 상단 배지, 하단 버튼바 같은 공통 템플릿 영역을
    비교 대상에서 제외하고 싶을 때 사용합니다.
    crop = {"top": 0.1, "bottom": 0.05, "left": 0.0, "right": 0.0}
    """
    if not crop:
        return pil_img
    w, h = pil_img.size
    left = int(w * crop.get("left", 0))
    right = int(w * (1 - crop.get("right", 0)))
    top = int(h * crop.get("top", 0))
    bottom = int(h * (1 - crop.get("bottom", 0)))

    # 잘라낸 뒤 남는 영역이 너무 작아지지 않도록 안전장치
    if right - left < 10 or bottom - top < 10:
        return pil_img
    return pil_img.crop((left, top, right, bottom))


def compute_phash_similarity(img_a: Image.Image, img_b: Image.Image) -> float:
    """
    perceptual hash 기반 유사도 (0~1, 1이 완전 동일).
    리사이즈/압축 정도만 다른 '동일 소재' 탐지에 강함.
    """
    hash_a = imagehash.phash(img_a, hash_size=16)
    hash_b = imagehash.phash(img_b, hash_size=16)
    max_bits = len(hash_a.hash) ** 2  # 16x16 = 256 bits
    distance = hash_a - hash_b
    return 1 - (distance / max_bits)


def compute_color_similarity(img_a_cv, img_b_cv) -> float:
    """HSV 색상 히스토그램 상관관계 기반 색감 유사도 (0~1)."""
    hsv_a = cv2.cvtColor(img_a_cv, cv2.COLOR_BGR2HSV)
    hsv_b = cv2.cvtColor(img_b_cv, cv2.COLOR_BGR2HSV)

    # 빈(bin) 개수를 너무 세밀하게 잡으면 배경이 단색에 가까운 이미지에서
    # JPEG 압축 노이즈만으로도 히스토그램이 크게 흔들려 유사도가 왜곡될 수 있어
    # 16x16 정도로 완만하게 잡습니다.
    hist_a = cv2.calcHist([hsv_a], [0, 1], None, [16, 16], [0, 180, 0, 256])
    hist_b = cv2.calcHist([hsv_b], [0, 1], None, [16, 16], [0, 180, 0, 256])

    cv2.normalize(hist_a, hist_a, 0, 1, cv2.NORM_MINMAX)
    cv2.normalize(hist_b, hist_b, 0, 1, cv2.NORM_MINMAX)

    correlation = cv2.compareHist(hist_a, hist_b, cv2.HISTCMP_CORREL)
    return max(0.0, (correlation + 1) / 2)  # -1~1 -> 0~1 정규화


def compute_structure_similarity(img_a_cv, img_b_cv, size=(256, 256)) -> float:
    """
    구도/레이아웃 유사도 (SSIM, 0~1).
    작은 사이즈로 리사이즈 후 흑백 변환하여 비교함으로써
    텍스트 등 세부 디테일 차이보다 전체적인 구조를 우선 반영합니다.
    """
    gray_a = cv2.cvtColor(cv2.resize(img_a_cv, size), cv2.COLOR_BGR2GRAY)
    gray_b = cv2.cvtColor(cv2.resize(img_b_cv, size), cv2.COLOR_BGR2GRAY)
    score, _ = ssim(gray_a, gray_b, full=True)
    return max(0.0, score)


def _get_clip_model(model_name="ViT-B-32", pretrained="openai"):
    """
    CLIP 모델을 한 번만 불러와서 재사용합니다 (지연 로딩).
    처음 호출될 때 인터넷에서 사전학습 가중치를 받아오며(수백MB, 최초 1회만),
    그 이후로는 로컬에 캐시되어 오프라인으로 계속 사용할 수 있습니다.
    """
    if _CLIP_STATE["model"] is None:
        model, _, preprocess = open_clip.create_model_and_transforms(
            model_name, pretrained=pretrained
        )
        model.eval()
        _CLIP_STATE["model"] = model
        _CLIP_STATE["preprocess"] = preprocess
    return _CLIP_STATE["model"], _CLIP_STATE["preprocess"]


def compute_clip_similarity(img_a_pil: Image.Image, img_b_pil: Image.Image,
                             model_name="ViT-B-32", pretrained="openai") -> float:
    """
    CLIP 이미지 임베딩 기반 '내용/의미' 유사도 (0~1).
    색상·구도가 아니라 이미지에 실제로 담긴 내용(피사체, 분위기, 맥락)이
    비슷한지를 비교합니다. 예: 같은 남색 배경이라도 사람 사진 vs 그래픽
    일러스트처럼 내용이 다르면 낮은 점수가 나옵니다.
    """
    model, preprocess = _get_clip_model(model_name, pretrained)
    with torch.no_grad():
        tensor_a = preprocess(img_a_pil).unsqueeze(0)
        tensor_b = preprocess(img_b_pil).unsqueeze(0)
        emb_a = model.encode_image(tensor_a)
        emb_b = model.encode_image(tensor_b)
        emb_a = emb_a / emb_a.norm(dim=-1, keepdim=True)
        emb_b = emb_b / emb_b.norm(dim=-1, keepdim=True)
        cos_sim = (emb_a @ emb_b.T).item()
    # 코사인 유사도(-1~1)를 0~1로 정규화
    return max(0.0, min(1.0, (cos_sim + 1) / 2))


def _single_direction_containment(template_color, scene_color, scales,
                                   min_dim=30, min_std=12.0):
    """
    template_color 를 여러 배율로 리사이즈하며 scene_color 안에서 가장 잘 맞는 위치를 찾음.
    (둘 다 BGR 컬러 이미지)

    - 흑백으로 변환해서 매칭하면 색이 완전히 달라도 명암 경계 패턴만 비슷하면
      허위로 높은 점수가 나올 수 있어(예: 빨강/초록 경계 vs 남색/흰색 경계),
      컬러 채널을 그대로 사용해 매칭합니다.
    - 단색/저대비 영역끼리는 정규화 상관계수(NCC)가 통계적으로 비정상 상승해
      '허위 매칭'이 나오기 쉬우므로, 너무 작은 템플릿이나 명암 대비(표준편차)가
      낮은(=거의 단색인) 템플릿은 매칭 대상에서 제외합니다.
    """
    h_scene, w_scene = scene_color.shape[:2]
    h_tpl, w_tpl = template_color.shape[:2]
    if h_tpl == 0 or w_tpl == 0 or h_scene == 0 or w_scene == 0:
        return 0.0

    gray_scene_for_std = cv2.cvtColor(scene_color, cv2.COLOR_BGR2GRAY)

    best_score = 0.0
    for scale in scales:
        new_w = max(1, int(w_tpl * scale))
        new_h = max(1, int(h_tpl * scale))
        # 템플릿이 장면(scene)보다 크면 매칭 불가하므로 건너뜀
        if new_w >= w_scene or new_h >= h_scene or new_w < min_dim or new_h < min_dim:
            continue
        resized_tpl = cv2.resize(template_color, (new_w, new_h))
        resized_tpl_gray = cv2.cvtColor(resized_tpl, cv2.COLOR_BGR2GRAY)

        # 거의 단색인 템플릿은 어디에 매칭해도 상관계수가 비정상적으로 높게
        # 나올 수 있으므로 건너뜁니다 (허위 양성 방지).
        if resized_tpl_gray.std() < min_std:
            continue

        result = cv2.matchTemplate(scene_color, resized_tpl, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)

        # 매칭된 실제 장면 영역도 단색이면 마찬가지로 신뢰할 수 없으므로 제외
        mx, my = max_loc
        matched_region_gray = gray_scene_for_std[my:my + new_h, mx:mx + new_w]
        if matched_region_gray.size == 0 or matched_region_gray.std() < min_std:
            continue

        if max_val > best_score:
            best_score = max_val
    return max(0.0, best_score)


def compute_containment_score(img_a_cv, img_b_cv, max_dim=800) -> float:
    """
    A가 B의 일부에 포함되어 있는지(또는 그 반대) 감지하는 점수 (0~1).
    전체 이미지가 아니라 '일부 영역'으로 재사용된 경우를 잡기 위한 지표로,
    멀티스케일 템플릿 매칭을 양방향(A→B, B→A)으로 수행해 더 높은 쪽을 사용합니다.
    """
    def shrink(img_cv):
        h, w = img_cv.shape[:2]
        longest = max(h, w)
        if longest <= max_dim:
            return img_cv
        scale = max_dim / longest
        return cv2.resize(img_cv, (max(1, int(w * scale)), max(1, int(h * scale))))

    img_a_small = shrink(img_a_cv)
    img_b_small = shrink(img_b_cv)

    scales = np.linspace(0.1, 1.5, 15)

    score_a_in_b = _single_direction_containment(img_a_small, img_b_small, scales)  # A가 B 안에?
    score_b_in_a = _single_direction_containment(img_b_small, img_a_small, scales)  # B가 A 안에?

    return max(score_a_in_b, score_b_in_a)


def classify(reference_path: Path, target_path: Path, weights, thresholds, pdf_dpi=200, crop=None,
             use_containment=True, use_clip=True, clip_model="ViT-B-32", clip_pretrained="openai"):
    img_a_pil, img_a_cv = load_creative(reference_path, pdf_dpi=pdf_dpi)
    img_b_pil, img_b_cv = load_creative(target_path, pdf_dpi=pdf_dpi)

    if crop:
        img_a_pil = crop_image(img_a_pil, crop)
        img_b_pil = crop_image(img_b_pil, crop)
        img_a_cv = cv2.cvtColor(np.array(img_a_pil), cv2.COLOR_RGB2BGR)
        img_b_cv = cv2.cvtColor(np.array(img_b_pil), cv2.COLOR_RGB2BGR)

    # 실제로 계산에 성공한 지표만 모아서, 그 지표들의 가중치만으로 재정규화합니다.
    # (예: CLIP을 못 쓰면 phash/color/structure 가중치 비율 그대로 나머지가 100%를 나눠 가짐)
    components = {
        "phash": compute_phash_similarity(img_a_pil, img_b_pil),
        "color": compute_color_similarity(img_a_cv, img_b_cv),
        "structure": compute_structure_similarity(img_a_cv, img_b_cv),
    }

    clip_sim = None
    if use_clip:
        if CLIP_AVAILABLE:
            try:
                clip_sim = compute_clip_similarity(img_a_pil, img_b_pil, clip_model, clip_pretrained)
                components["clip"] = clip_sim
            except Exception as e:
                print(f"[경고] CLIP 유사도 계산에 실패해 이 지표 없이 진행합니다: {e}")
        else:
            _warn_clip_unavailable()

    weight_sum = sum(weights.get(k, 0) for k in components) or 1.0
    total_score = sum(weights.get(k, 0) * v for k, v in components.items()) / weight_sum

    phash_sim = components["phash"]
    color_sim = components["color"]
    structure_sim = components["structure"]

    if total_score >= thresholds["identical"]:
        holistic_category = "동일"
    elif total_score >= thresholds["similar"]:
        holistic_category = "유사"
    else:
        holistic_category = "다름"

    containment_score = None
    category = holistic_category
    final_score = total_score
    matched_by = "전체유사도"

    if use_containment:
        # 원본 크롭 전 이미지(전체 이미지)를 기준으로 부분 포함을 탐지합니다.
        # (crop을 적용했다면 crop된 이미지로 이미 img_a_cv/img_b_cv가 대체된 상태이므로 그대로 사용)
        containment_score = compute_containment_score(img_a_cv, img_b_cv)

        containment_identical_th = thresholds.get("containment_identical", 0.95)
        containment_similar_th = thresholds.get("containment_similar", 0.80)

        if containment_score >= containment_identical_th:
            containment_category = "동일"
        elif containment_score >= containment_similar_th:
            containment_category = "유사"
        else:
            containment_category = "다름"

        priority = {"동일": 2, "유사": 1, "다름": 0}
        if priority[containment_category] > priority[holistic_category]:
            category = containment_category
            final_score = max(total_score, containment_score)
            matched_by = "부분포함"

    return {
        "phash": round(phash_sim, 4),
        "color": round(color_sim, 4),
        "structure": round(structure_sim, 4),
        "clip": round(clip_sim, 4) if clip_sim is not None else None,
        "containment": round(containment_score, 4) if containment_score is not None else None,
        "total": round(total_score, 4),          # 전체 유사도 가중합 (참고용)
        "final_score": round(final_score, 4),     # 실제 분류에 사용된 점수
        "matched_by": matched_by,                 # "전체유사도" 또는 "부분포함"
        "category": category,
    }


def gather_targets(target_args):
    """--targets로 받은 경로들(파일 또는 폴더)에서 이미지/PDF 파일 목록을 모읍니다."""
    files = []
    for t in target_args:
        p = Path(t)
        if p.is_dir():
            for f in sorted(p.iterdir()):
                if f.suffix.lower() in SUPPORTED_EXTENSIONS:
                    files.append(f)
        elif p.is_file():
            if p.suffix.lower() in SUPPORTED_EXTENSIONS:
                files.append(p)
            else:
                print(f"[경고] 지원하지 않는 확장자, 건너뜁니다: {p}")
        else:
            print(f"[경고] 존재하지 않는 경로, 건너뜁니다: {p}")
    return files


def rename_file(path: Path, category: str, total_score: float, dry_run: bool):
    tag = {"동일": "[동일]", "유사": "[유사]", "다름": "[다름]"}[category]
    score_str = f"{total_score:.2f}"
    new_name = f"{tag}_{score_str}_{path.name}"
    new_path = path.with_name(new_name)

    if new_path.exists():
        print(f"[경고] 대상 파일명이 이미 존재하여 건너뜁니다: {new_path}")
        return path

    if dry_run:
        print(f"  (dry-run) {path.name}  ->  {new_name}")
    else:
        path.rename(new_path)
        print(f"  {path.name}  ->  {new_name}")
    return new_path


def main():
    parser = argparse.ArgumentParser(
        description="기준 광고소재(A)와 비교하여 대상 소재들을 동일/유사/다름으로 분류하고 파일명을 변경합니다."
    )
    parser.add_argument("--reference", "-r", required=True, help="기준이 되는 소재 A의 파일 경로")
    parser.add_argument(
        "--targets", "-t", nargs="+", required=True,
        help="비교할 대상 소재들의 파일 경로 또는 폴더 경로 (여러 개 가능)"
    )
    parser.add_argument("--dry-run", action="store_true", help="실제로 파일명을 바꾸지 않고 결과만 미리 확인")
    parser.add_argument("--identical-threshold", type=float, default=0.93, help="'동일' 판정 기준 점수 (기본값 0.93)")
    parser.add_argument("--similar-threshold", type=float, default=0.75, help="'유사' 판정 기준 점수 (기본값 0.75)")
    parser.add_argument(
        "--weights", type=float, nargs=4, metavar=("PHASH", "COLOR", "STRUCTURE", "CLIP"),
        default=[0.15, 0.15, 0.25, 0.45],
        help=(
            "각 지표의 가중치 (phash color structure clip 순, 합이 1이 아니어도 자동 정규화). "
            "CLIP을 못 쓰는 경우 나머지 3개 가중치 비율대로 자동 재분배됩니다. 기본값: 0.15 0.15 0.25 0.45"
        )
    )
    parser.add_argument(
        "--no-clip", action="store_true",
        help="CLIP(내용 기반 의미 유사도) 계산을 끕니다. torch/open_clip_torch가 없으면 자동으로 꺼집니다."
    )
    parser.add_argument("--clip-model", default="ViT-B-32", help="사용할 CLIP 모델 이름 (기본값 ViT-B-32)")
    parser.add_argument("--clip-pretrained", default="openai", help="CLIP 사전학습 가중치 태그 (기본값 openai)")
    parser.add_argument("--pdf-dpi", type=int, default=200, help="PDF를 이미지로 렌더링할 때 해상도 (기본값 200)")
    parser.add_argument(
        "--crop-top", type=float, default=0.0,
        help="상단에서 제외할 비율(%%), 로고/배지 등 공통 요소를 비교에서 빼고 싶을 때 사용. 기본값 0"
    )
    parser.add_argument("--crop-bottom", type=float, default=0.0, help="하단에서 제외할 비율(%%). 기본값 0")
    parser.add_argument("--crop-left", type=float, default=0.0, help="좌측에서 제외할 비율(%%). 기본값 0")
    parser.add_argument("--crop-right", type=float, default=0.0, help="우측에서 제외할 비율(%%). 기본값 0")
    parser.add_argument(
        "--no-containment", action="store_true",
        help="부분 포함(템플릿 매칭) 탐지를 끕니다. 기본은 켜져 있음"
    )
    parser.add_argument(
        "--containment-identical-threshold", type=float, default=0.95,
        help="부분 포함 매칭 점수가 이 값 이상이면 '동일'로 판정 (기본값 0.95)"
    )
    parser.add_argument(
        "--containment-similar-threshold", type=float, default=0.80,
        help="부분 포함 매칭 점수가 이 값 이상이면 '유사'로 판정 (기본값 0.80)"
    )
    parser.add_argument("--log", default="classification_result.csv", help="결과를 저장할 CSV 파일명")

    args = parser.parse_args()

    reference_path = Path(args.reference)
    if not reference_path.is_file():
        print(f"[오류] 기준 소재 파일을 찾을 수 없습니다: {reference_path}")
        sys.exit(1)
    if reference_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        print(f"[오류] 지원하지 않는 확장자입니다: {reference_path.suffix}")
        sys.exit(1)

    weights = {
        "phash": args.weights[0],
        "color": args.weights[1],
        "structure": args.weights[2],
        "clip": args.weights[3],
    }
    thresholds = {
        "identical": args.identical_threshold,
        "similar": args.similar_threshold,
        "containment_identical": args.containment_identical_threshold,
        "containment_similar": args.containment_similar_threshold,
    }
    crop = {
        "top": args.crop_top / 100,
        "bottom": args.crop_bottom / 100,
        "left": args.crop_left / 100,
        "right": args.crop_right / 100,
    }
    crop_active = any(v > 0 for v in crop.values())

    targets = gather_targets(args.targets)
    if not targets:
        print("[오류] 비교할 대상 이미지를 찾지 못했습니다.")
        sys.exit(1)

    # 기준 파일 자체가 대상 목록에 섞여 들어간 경우 제외
    targets = [t for t in targets if t.resolve() != reference_path.resolve()]

    use_clip = not args.no_clip
    print(f"기준 소재: {reference_path.name}")
    print(f"비교 대상: {len(targets)}개")
    print(
        f"가중치 -> phash: {weights['phash']:.2f}, color: {weights['color']:.2f}, "
        f"structure: {weights['structure']:.2f}, clip: {weights['clip']:.2f}"
        f"{' (CLIP 비활성화됨)' if not use_clip else ''}"
    )
    print(f"임계값 -> 동일: {thresholds['identical']}, 유사: {thresholds['similar']}")
    if crop_active:
        print(f"제외 영역 -> 상 {args.crop_top}% / 하 {args.crop_bottom}% / 좌 {args.crop_left}% / 우 {args.crop_right}%")
    if args.dry_run:
        print("*** DRY-RUN 모드: 실제 파일명은 변경되지 않습니다 ***")
    print("-" * 60)

    results = []
    for target_path in targets:
        try:
            result = classify(
                reference_path, target_path, weights, thresholds,
                pdf_dpi=args.pdf_dpi, crop=crop, use_containment=not args.no_containment,
                use_clip=use_clip, clip_model=args.clip_model, clip_pretrained=args.clip_pretrained,
            )
        except Exception as e:
            print(f"[오류] {target_path.name} 처리 중 문제 발생: {e}")
            continue

        matched_note = f", {result['matched_by']}로 판정" if result["matched_by"] == "부분포함" else ""
        containment_str = f" / containment {result['containment']}" if result["containment"] is not None else ""
        clip_str = f" / clip {result['clip']}" if result["clip"] is not None else ""
        print(
            f"{target_path.name} -> {result['category']}{matched_note} "
            f"(점수 {result['final_score']} / phash {result['phash']} / color {result['color']} "
            f"/ structure {result['structure']}{clip_str}{containment_str})"
        )
        new_path = rename_file(target_path, result["category"], result["final_score"], args.dry_run)

        results.append({
            "원본파일명": target_path.name,
            "변경된파일명": new_path.name,
            "분류": result["category"],
            "판정근거": result["matched_by"],
            "최종점수": result["final_score"],
            "종합유사도": result["total"],
            "phash점수": result["phash"],
            "color점수": result["color"],
            "structure점수": result["structure"],
            "clip점수": result["clip"],
            "containment점수": result["containment"],
        })

    if results:
        with open(args.log, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
            writer.writeheader()
            writer.writerows(results)
        print("-" * 60)
        print(f"결과 로그 저장됨: {args.log}")


if __name__ == "__main__":
    main()
