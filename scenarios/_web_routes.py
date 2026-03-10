"""
Locust 웹 UI 확장 — 모니터링 대시보드 라우트 및 배너 주입

import 시점에 @events.init 데코레이터가 자동으로 리스너를 등록합니다.
"""

import json
import os

from locust import events

from core.metrics_store import get_metrics_store

_metrics = get_metrics_store()


@events.init.add_listener
def on_init(environment, **kwargs):
    """Locust 웹 서버에 모니터링 대시보드 라우트를 추가합니다."""
    if environment.web_ui is None:
        return

    from flask import Response

    _template_dir = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "templates"
    )

    @environment.web_ui.app.route("/monitor")
    def monitor_page():
        html_path = os.path.join(_template_dir, "monitor.html")
        with open(html_path, "r", encoding="utf-8") as f:
            return Response(f.read(), content_type="text/html; charset=utf-8")

    @environment.web_ui.app.route("/monitor/data")
    def monitor_data():
        data = _metrics.snapshot()
        data["mode"] = _metrics.mode
        return Response(
            json.dumps(data), content_type="application/json"
        )

    # Locust 메인 페이지에 Monitor 링크 배너를 주입하는 미들웨어
    _original_app = environment.web_ui.app

    @_original_app.after_request
    def inject_monitor_link(response):
        if response.content_type and "text/html" in response.content_type:
            inject_html = (
                '<div id="monitor-banner" style="'
                'position:fixed;top:0;left:0;right:0;z-index:9999;'
                'background:#16213e;border-bottom:2px solid #00d4aa;'
                'padding:6px 16px;text-align:center;font-size:13px;'
                'font-family:Segoe UI,sans-serif;">'
                '<a href="/monitor" style="color:#00d4aa;text-decoration:none;font-weight:bold;">'
                '&#128202; CUBRID Stress Monitor Dashboard &rarr;'
                '</a></div>'
                '<script>document.body.style.paddingTop="36px";</script>'
            )
            data = response.get_data(as_text=True)
            if '/monitor' not in data and '<body' in data.lower():
                data = data.replace('</body>', inject_html + '</body>')
                response.set_data(data)
        return response

    print("[Init] 모니터링 대시보드: http://localhost:8089/monitor")
