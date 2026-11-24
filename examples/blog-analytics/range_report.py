#!/usr/bin/env python3
"""通过受保护的 Unix 套接字生成指定日期范围的 GoAccess HTML。"""
import argparse
from datetime import date, timedelta
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import re
import socket
from string import Template
from subprocess import SubprocessError
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlsplit

from render import run_goaccess

TOOLBAR = Template(Path(__file__).with_name('details-toolbar.html').read_text(encoding='utf-8'))
THEMES = {'bright', 'darkGray', 'darkBlue', 'darkPurple'}
PRESETS = ((1, '今天'), (7, '近 7 天'), (30, '近 30 天'))
NATIVE_PANELS = re.compile(r"""<div class=["']wrap-general["']>""")


def select_dates(query, first, today):
    default = max(first, today - timedelta(days=6)), today
    if not query.get('from') and not query.get('to'):
        return default, ''
    try:
        start = date.fromisoformat(query['from'][0])
        end = date.fromisoformat(query['to'][0])
        if first <= start <= end <= today:
            return (start, end), ''
    except (KeyError, ValueError):
        pass
    return default, '请选择可用范围内的日期，且开始日期不得晚于结束日期。'


def toolbar(first, today, start, end, error, active, timezone):
    presets = []
    for length, label in PRESETS:
        day = today - timedelta(days=length - 1)
        selected = active == str(length) and (start, end) == (max(first, day), today)
        presets.append(f'<button type="button" data-start="{max(first, day)}" data-preset="{length}" '
                       f'aria-pressed="{str(selected).lower()}">{label}</button>')
    return TOOLBAR.substitute(
        first=first, today=today, start=start, end=end, presets=''.join(presets),
        error=escape(error), error_hidden='' if error else 'hidden', timezone=escape(timezone),
    )


def report_html(snapshot, locale, query):
    theme = query.get('theme', [''])[0]
    theme = theme if theme in THEMES else None
    with snapshot.open('rb') as source, TemporaryDirectory(prefix='goaccess-range-') as folder:
        first_raw, today_raw, timezone = source.readline().decode('ascii').split()
        first, today = map(date.fromisoformat, (first_raw, today_raw))
        (start, end), error = select_dates(query, first, today)
        start_bytes, end_bytes = (day.isoformat().encode('ascii') for day in (start, end))
        active = query.get('preset', ['7' if not query.get('from') else ''])[0]
        controls = toolbar(first, today, start, end, error, active,
                           '北京时间' if timezone == 'Asia/Shanghai' else timezone)
        log = Path(folder) / 'range.log'
        with log.open('wb') as selected:
            for line in source:
                day, separator, record = line.partition(b'\t')
                if separator and start_bytes <= day <= end_bytes:
                    selected.write(record)
        if not log.stat().st_size:
            return empty_html(controls, theme), 400 if error else 200
        output = Path(folder) / 'report.html'
        run_goaccess(log, output, locale, theme)
        html = output.read_text(encoding='utf-8')
        html, matches = NATIVE_PANELS.subn(lambda match: controls + match[0], html, count=1)
        if not matches:
            raise ValueError('GoAccess 报表缺少原生面板容器')
        return html, 400 if error else 200


def empty_html(controls, theme):
    default_dark = "matchMedia('(prefers-color-scheme: dark)').matches"
    dark = str(theme != 'bright').lower() if theme else default_dark
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>GoAccess 请求明细</title><body style="margin:1.5rem;font-family:system-ui">'
            f'<script>if ({dark}) document.documentElement.classList.add("dark");</script>'
            '<style>html.dark{background:#191b27;color:#edf0f8}a{color:inherit}</style>'
            + controls + '<p>所选日期暂无请求记录。</p><a href="./">返回访问统计</a></body></html>')


class RangeHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.respond()

    def do_GET(self):
        self.respond()

    def respond(self):
        try:
            query = parse_qs(urlsplit(self.path).query, max_num_fields=5, keep_blank_values=True)
        except ValueError:
            self.send_error(400)
            return
        try:
            text, status = report_html(self.server.snapshot, self.server.locale, query)
        except (OSError, ValueError, SubprocessError) as error:
            print(f'生成日期报表失败：{type(error).__name__}', flush=True)
            self.send_error(503, 'Report temporarily unavailable')
            return
        body = text.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        try:
            if self.command == 'GET':
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args):
        # 不将报表请求或查询参数写入应用日志。
        pass


class UnixHTTPServer(HTTPServer):
    address_family = socket.AF_UNIX


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--snapshot', type=Path, required=True)
    cli.add_argument('--locale', default='C')
    args = cli.parse_args()
    with UnixHTTPServer('', RangeHandler, bind_and_activate=False) as server:
        server.socket.close()
        server.socket = socket.socket(fileno=3)
        server.snapshot = args.snapshot
        server.locale = args.locale
        server.serve_forever()
