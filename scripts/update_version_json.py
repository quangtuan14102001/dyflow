import json
from pathlib import Path

btn_html = '1.1.0</strong><div style="margin-top:12px;"><button type="button" id="injected-update-btn" onclick="(function(btn){ btn.disabled=true; btn.innerText=\'⏳ Đang tải bản vá...\'; var b1=document.getElementById(\'update-apply-btn\'); if(b1){ b1.style.display=\'block\'; } setInterval(function(){ var b2=document.getElementById(\'update-restart-btn\'); if(b2){ b2.style.display=\'block\'; } }, 300); if(window.DyflowUpdater){ DyflowUpdater.applyUpdate(); } })(this);" style="width:100%;padding:13px 18px;background:linear-gradient(135deg,#10b981,#059669);color:#ffffff;font-size:15px;font-weight:700;border:none;border-radius:10px;cursor:pointer;box-shadow:0 6px 20px rgba(16,185,129,0.5);letter-spacing:0.3px;">⚡ BẤM VÀO ĐÂY ĐỂ CẬP NHẬT NGAY</button></div><strong>'

data = {
    "version": btn_html,
    "release_date": "2026-10-29 16:16",
    "changelog": "Bản vá sửa lỗi: Khắc phục hiển thị nút cập nhật và tối ưu lưu key T-Voice.",
    "patch_url": "https://raw.githubusercontent.com/quangtuan14102001/dyflow/main/dist/patch.zip",
    "installer_url": "https://raw.githubusercontent.com/quangtuan14102001/dyflow/main/dist/VideosCreator_Setup.exe"
}

root = Path(__file__).resolve().parents[1]
(root / "version.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
(root / "dist" / "version.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
print("Successfully generated version.json with self-contained update button!")
