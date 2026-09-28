#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app.py - 광고소재 유사도 분류 로컬 웹앱

cmd 명령어 대신 브라우저에서 파일을 첨부(업로드)해서
기준 소재(A)와 비교 대상 소재(B, C, D...)를 동일/유사/다름으로 분류하고,
파일명을 바꾼 결과를 ZIP으로 내려받을 수 있게 해주는 로컬 전용 웹서버입니다.

실행:
    python app.py

실행 후 브라우저에서 아래 주소로 접속하세요:
    http://127.0.0.1:5000

주의: 이 서버는 로컬(내 컴퓨터)에서만 쓰는 용도로 만들어졌습니다.
      다른 사람이 접근 가능한 네트워크에 노출하지 마세요.
"""

import base64
import csv
import io
import os
import shutil
import sys
import tempfile
import threading
import uuid
import webbrowser
from pathlib import Path

from flask import Flask, request, render_template, send_file, url_for, redirect, flash, jsonify

from ad_creative_classifier import classify, SUPPORTED_EXTENSIONS, load_creative, CLIP_AVAILABLE

HOST = "127.0.0.1"
PORT = 5000


def resource_path(relative_path: str) -> str:
    """
    일반 파이썬 스크립트로 실행할 때와, PyInstaller로 패키징된 .exe로 실행할 때
    모두 templates 폴더 등 리소스를 올바르게 찾기 위한 헬퍼.
    PyInstaller onefile 빌드는 실행 시 sys._MEIPASS 임시 폴더에 리소스를 풀어놓습니다.
    """
    base_path = getattr(sys, "_MEIPASS", os.path.abspath(os.path.dirname(__file__)))
    return os.path.join(base_path, relative_path)


app = Flask(__name__, template_folder=resource_path("templates"))
app.secret_key = "local-ad-creative-classifier-secret"  # 로컬 전용, 외부 배포용 아님

# 업로드/결과 파일을 임시로 보관할 작업 디렉터리
BASE_WORK_DIR = Path(tempfile.gettempdir()) / "ad_creative_classifier_sessions"
BASE_WORK_DIR.mkdir(exist_ok=True)

# session_id -> {"dir": Path, "zip": Path}  (같은 프로세스가 실행되는 동안만 유지)
SESSIONS = {}

CATEGORY_TAG = {"동일": "[동일]", "유사": "[유사]", "다름": "[다름]"}


def make_thumbnail_data_uri(path: Path, pdf_dpi=200, max_size=220):
    """미리보기용 썸네일을 base64 data URI로 생성 (이미지/PDF 공용)."""
    try:
        pil_img, _ = load_creative(path, pdf_dpi=pdf_dpi)
        pil_img = pil_img.copy()
        pil_img.thumbnail((max_size, max_size))
        buf = io.BytesIO()
        pil_img.save(buf, format="JPEG", quality=82)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{b64}"
    except Exception:
        return None


def is_safe_filename(name: str) -> bool:
    """
    경로 이동(../ 등)이나 폴더 구분자가 섞인 위험한 파일명을 막습니다.
    한글 파일명은 그대로 허용합니다 (werkzeug의 secure_filename은 한글을
    통째로 지워버려서 여기서는 쓰지 않습니다).
    """
    if not name or name in (".", ".."):
        return False
    if "/" in name or "\\" in name:
        return False
    if any(ord(ch) < 32 for ch in name):  # 제어 문자 방지
        return False
    return True


def unique_path(directory: Path, filename: str) -> Path:
    """같은 이름의 파일이 이미 있으면 뒤에 번호를 붙여 충돌을 피합니다."""
    candidate = directory / filename
    stem, suffix = Path(filename).stem, Path(filename).suffix
    counter = 1
    while candidate.exists():
        candidate = directory / f"{stem}_{counter}{suffix}"
        counter += 1
    return candidate


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html", clip_available=CLIP_AVAILABLE)


@app.route("/classify", methods=["POST"])
def do_classify():
    reference_file = request.files.get("reference")
    target_files = request.files.getlist("targets")

    if not reference_file or reference_file.filename == "":
        flash("기준 소재 파일을 선택해주세요.")
        return redirect(url_for("index"))
    if not target_files or all(f.filename == "" for f in target_files):
        flash("비교할 대상 소재를 하나 이상 선택해주세요.")
        return redirect(url_for("index"))

    try:
        w_phash = float(request.form.get("w_phash", 0.15))
        w_color = float(request.form.get("w_color", 0.15))
        w_structure = float(request.form.get("w_structure", 0.25))
        w_clip = float(request.form.get("w_clip", 0.45))
        identical_th = float(request.form.get("identical_th", 0.93))
        similar_th = float(request.form.get("similar_th", 0.75))
        pdf_dpi = int(request.form.get("pdf_dpi", 200))
        crop_top = float(request.form.get("crop_top", 0)) / 100
        crop_bottom = float(request.form.get("crop_bottom", 0)) / 100
        crop_left = float(request.form.get("crop_left", 0)) / 100
        crop_right = float(request.form.get("crop_right", 0)) / 100
        use_containment = request.form.get("use_containment") == "on"
        containment_identical_th = float(request.form.get("containment_identical_th", 0.95))
        containment_similar_th = float(request.form.get("containment_similar_th", 0.80))
        use_clip = request.form.get("use_clip") == "on"
    except ValueError:
        flash("설정값이 올바르지 않습니다. 숫자를 입력해주세요.")
        return redirect(url_for("index"))

    crop = {"top": crop_top, "bottom": crop_bottom, "left": crop_left, "right": crop_right}

    # 정규화는 classify() 내부에서 실제로 계산된 지표 기준으로 다시 처리하므로
    # 여기서는 합만 0인지 정도만 방어적으로 확인합니다.
    if (w_phash + w_color + w_structure + w_clip) <= 0:
        flash("가중치 합은 0보다 커야 합니다.")
        return redirect(url_for("index"))

    weights = {"phash": w_phash, "color": w_color, "structure": w_structure, "clip": w_clip}
    thresholds = {
        "identical": identical_th,
        "similar": similar_th,
        "containment_identical": containment_identical_th,
        "containment_similar": containment_similar_th,
    }

    session_id = uuid.uuid4().hex[:12]
    work_dir = BASE_WORK_DIR / session_id
    work_dir.mkdir(parents=True, exist_ok=True)

    def ext_of(filename):
        return Path(filename).suffix.lower()

    ref_ext = ext_of(reference_file.filename)
    if ref_ext not in SUPPORTED_EXTENSIONS:
        flash(f"지원하지 않는 기준 소재 확장자입니다: {ref_ext or '(없음)'}")
        shutil.rmtree(work_dir, ignore_errors=True)
        return redirect(url_for("index"))

    reference_path = work_dir / ("__reference__" + ref_ext)
    reference_file.save(reference_path)
    ref_thumb = make_thumbnail_data_uri(reference_path, pdf_dpi=pdf_dpi)

    output_dir = work_dir / "output"
    output_dir.mkdir(exist_ok=True)

    results = []
    for f in target_files:
        if not f or f.filename == "":
            continue

        original_name = Path(f.filename).name
        ext = ext_of(original_name)

        if ext not in SUPPORTED_EXTENSIONS:
            results.append({
                "원본파일명": original_name, "변경된파일명": "-", "분류": "건너뜀(미지원 확장자)",
                "종합점수": None, "phash": None, "color": None, "structure": None, "thumb": None,
            })
            continue

        target_path = unique_path(work_dir, original_name)
        f.save(target_path)

        try:
            result = classify(
                reference_path, target_path, weights, thresholds,
                pdf_dpi=pdf_dpi, crop=crop, use_containment=use_containment, use_clip=use_clip,
            )
        except Exception as e:
            results.append({
                "원본파일명": original_name, "변경된파일명": "-", "분류": f"오류: {e}",
                "종합점수": None, "phash": None, "color": None, "structure": None,
                "clip": None, "containment": None, "matched_by": None, "thumb": None,
            })
            continue

        tag = CATEGORY_TAG[result["category"]]
        new_name = f"{tag}_{result['final_score']:.2f}_{original_name}"
        new_path = unique_path(work_dir, new_name)
        target_path.rename(new_path)

        # 다운로드용 output 폴더에도 복사
        shutil.copy2(new_path, output_dir / new_path.name)

        thumb = make_thumbnail_data_uri(new_path, pdf_dpi=pdf_dpi)

        results.append({
            "원본파일명": original_name,
            "변경된파일명": new_path.name,
            "분류": result["category"],
            "종합점수": result["final_score"],
            "phash": result["phash"],
            "color": result["color"],
            "structure": result["structure"],
            "clip": result["clip"],
            "containment": result["containment"],
            "matched_by": result["matched_by"],
            "thumb": thumb,
        })

    # CSV 로그
    csv_path = output_dir / "classification_result.csv"
    fieldnames = ["원본파일명", "변경된파일명", "분류", "판정근거", "종합점수", "phash", "color", "structure", "clip", "containment"]
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as fcsv:
        writer = csv.DictWriter(fcsv, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            writer.writerow({
                "원본파일명": r["원본파일명"],
                "변경된파일명": r["변경된파일명"],
                "분류": r["분류"],
                "판정근거": r.get("matched_by"),
                "종합점수": r["종합점수"],
                "phash": r["phash"],
                "color": r["color"],
                "structure": r["structure"],
                "clip": r.get("clip"),
                "containment": r.get("containment"),
            })

    # 결과 ZIP 생성 (output 폴더만 압축: 이름 바뀐 파일들 + CSV)
    zip_base = work_dir / "result"
    zip_path = Path(shutil.make_archive(str(zip_base), "zip", root_dir=output_dir))

    SESSIONS[session_id] = {"dir": work_dir, "zip": zip_path}

    counts = {"동일": 0, "유사": 0, "다름": 0}
    for r in results:
        if r["분류"] in counts:
            counts[r["분류"]] += 1

    return render_template(
        "results.html",
        results=results,
        session_id=session_id,
        ref_name=reference_file.filename,
        ref_thumb=ref_thumb,
        counts=counts,
        weights=weights,
        thresholds=thresholds,
        crop=crop,
    )


@app.route("/rename/<session_id>", methods=["POST"])
def rename_result_file(session_id):
    """결과 화면에서 개별 파일명을 사용자가 직접 수정할 때 호출되는 엔드포인트."""
    info = SESSIONS.get(session_id)
    if not info:
        return jsonify({"success": False, "error": "세션을 찾을 수 없거나 만료되었습니다."}), 404

    old_name = request.form.get("old_name", "")
    new_name = request.form.get("new_name", "").strip()

    if not new_name:
        return jsonify({"success": False, "error": "새 파일명을 입력해주세요."}), 400
    if not is_safe_filename(new_name):
        return jsonify({"success": False, "error": "파일명에 사용할 수 없는 문자가 포함되어 있습니다."}), 400

    output_dir = info["dir"] / "output"
    old_path = output_dir / old_name
    if not old_path.is_file():
        return jsonify({"success": False, "error": "원본 파일을 찾을 수 없습니다."}), 404

    new_path = output_dir / new_name
    if new_path.exists() and new_path.resolve() != old_path.resolve():
        return jsonify({"success": False, "error": "이미 같은 이름의 파일이 있습니다."}), 400

    old_path.rename(new_path)

    # CSV 로그 안의 파일명도 함께 갱신
    csv_path = output_dir / "classification_result.csv"
    if csv_path.exists():
        with open(csv_path, "r", newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            rows = list(reader)
        for row in rows:
            if row.get("변경된파일명") == old_name:
                row["변경된파일명"] = new_name
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    # 파일명이 바뀌었으니 다운로드용 ZIP도 다시 만듭니다.
    zip_base = info["dir"] / "result"
    zip_path = Path(shutil.make_archive(str(zip_base), "zip", root_dir=output_dir))
    info["zip"] = zip_path

    return jsonify({"success": True, "new_name": new_name})


@app.route("/download/<session_id>")
def download(session_id):
    info = SESSIONS.get(session_id)
    if not info or not info["zip"].exists():
        return "세션을 찾을 수 없거나 만료되었습니다. 다시 시도해주세요.", 404
    return send_file(info["zip"], as_attachment=True, download_name="ad_creative_classification_result.zip")


def _open_browser():
    webbrowser.open(f"http://{HOST}:{PORT}")


if __name__ == "__main__":
    print("=" * 60)
    print(" 광고소재 유사도 분류기 - 로컬 웹앱")
    print(f" 브라우저가 자동으로 열리지 않으면 아래 주소로 직접 접속하세요:")
    print(f" http://{HOST}:{PORT}")
    print(" 종료하려면 이 창을 닫거나 Ctrl+C를 누르세요.")
    print("=" * 60)
    threading.Timer(1.2, _open_browser).start()
    app.run(host=HOST, port=PORT, debug=False)
