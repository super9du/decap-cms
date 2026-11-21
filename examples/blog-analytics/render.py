#!/usr/bin/env python3
"""从单站点的 Nginx 日志生成聚合看板及 GoAccess 明细。"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta
import gzip
from html import escape, unescape
from pathlib import Path
import re
from string import Template
import subprocess
from tempfile import TemporaryDirectory
from urllib.parse import unquote
from zoneinfo import ZoneInfo

TEMPLATE = Path(__file__).with_name('dashboard.html')
LOG_PATTERN = re.compile(
    r'(?P<ip>\S+) - - \[(?P<stamp>[^\]]+)\] '
    r'"(?P<method>\S+) (?P<path>/\S*) HTTP/[^"]+" (?P<status>\d{3}) \d+ "-" "(?P<agent>[^"]*)"'
)
BOT = re.compile(r'bot\b|crawl|spider|slurp|headless|curl/|wget/|python-requests|^-$', re.I)
PAGE = re.compile(r'^/(?:[^/.]+/)*(?:[^/.]+\.html?)?$')
TITLE = re.compile(r'<title\b[^>]*>(.*?)</title\s*>', re.I | re.S)


def log_lines(log):
    for path in [log, *sorted(log.parent.glob(log.name + '.[0-9]*'))]:
        try:
            with (gzip.open if path.suffix == '.gz' else open)(
                path, mode='rt', encoding='utf-8', errors='replace'
            ) as source:
                yield from source
        except FileNotFoundError:
            # 文件可能恰好被日志轮转移走，下次刷新会从新文件读取。
            continue


def date_label(date):
    return f'{date.month}/{date.day}'


def columns_html(days, visitors, since, axis_max):
    columns = []
    for day in days:
        date = date_label(day)
        count = len(visitors[day])
        display = f'{count:,}' if day >= since.date() else '—'
        description = f'{date}，{display} 人' if display != '—' else f'{date}，暂无记录'
        today = ' current-day' if day == days[-1] else ''
        columns.append(f'''<div class="column{today}" tabindex="0" role="img" aria-label="{description}">
      <div class="bar" style="--height:{count / axis_max:.3%}" aria-hidden="true">
        <span class="value num">{display}</span></div>
      <span class="day" aria-hidden="true">{date}</span></div>''')
    return '\n'.join(columns)


def article_title(root, path):
    fallback = unquote(path).rstrip('/').split('/')[-1]
    file = (root / (unquote(path).lstrip('/') + ('index.html' if path.endswith('/') else ''))).resolve()
    if not file.is_relative_to(root) or not file.is_file():
        return fallback
    with file.open(encoding='utf-8', errors='replace') as source:
        match = TITLE.search(source.read(32768))
    return unescape(match.group(1)).strip() if match else fallback


def articles_html(root, counter):
    if not counter:
        return '<p class="empty">近 7 天暂无文章访问记录。</p>'
    articles = sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:8]
    maximum = articles[0][1]
    rows = []
    for rank, (path, count) in enumerate(articles, start=1):
        title = escape(article_title(root, path))
        rows.append(f'''<tr><td class="rank num">{rank}</td><td class="article-title">{title}</td>
      <td><div class="count-wrap"><span class="meter" aria-hidden="true">
      <i style="--share:{count / maximum:.0%}"></i></span><span class="count-num num">{count:,}</span></div></td></tr>''')
    return '''<table aria-labelledby="articles"><thead><tr><th scope="col" class="rank">排名</th>
      <th scope="col">文章</th><th scope="col" class="count">浏览次数</th></tr></thead><tbody>''' + '\n'.join(rows) + '</tbody></table>'


def render(args, now):
    since = datetime.fromisoformat(args.since).astimezone(now.tzinfo)
    days = [now.date() - timedelta(days=offset) for offset in range(6, -1, -1)]
    yesterday = days[-2]
    yesterday_cutoff = now - timedelta(days=1)
    visitors = defaultdict(set)
    previous = set()
    articles = Counter()
    root = args.public_root.resolve(strict=True)
    args.output.mkdir(parents=True, exist_ok=True)

    with TemporaryDirectory(prefix='.refresh-', dir=args.output) as temporary:
        stage = Path(temporary)
        log = stage / 'sanitized.log'
        with log.open('w', encoding='utf-8') as report:
            for line in log_lines(args.log):
                match = LOG_PATTERN.fullmatch(line.rstrip('\n'))
                if not match:
                    continue
                hit = match.groupdict()
                moment = datetime.strptime(hit['stamp'], '%d/%b/%Y:%H:%M:%S %z').astimezone(now.tzinfo)
                if not since <= moment <= now or moment.date() < days[0]:
                    continue
                path = hit['path']
                if path.startswith(('/admin/', '/admin-auth/')) or path in ('/admin', '/github-webhook/'):
                    continue
                # 详细报表不包含来源、查询参数或完整 IP。
                report.write(line.rstrip('\n') + '\n')
                if (hit['method'] != 'GET' or hit['status'] not in ('200', '304')
                        or not PAGE.fullmatch(path) or BOT.search(hit['agent'])):
                    continue
                if path.endswith('/index.html'):
                    path = path.removesuffix('index.html')
                visitor = (hit['ip'], hit['agent'])
                visitors[moment.date()].add(visitor)
                if moment.date() == yesterday and moment <= yesterday_cutoff:
                    previous.add(visitor)
                if path.startswith(args.article_prefix) and path != args.article_prefix:
                    articles[path] += 1

        today_total = len(visitors[days[-1]])
        if since > datetime.combine(yesterday, time.min, tzinfo=now.tzinfo):
            comparison = '采集满一个自然日后显示昨日同期对比'
        elif previous:
            delta = today_total - len(previous)
            comparison = f'较昨日同期 <strong>{delta:+,} 人（{delta / len(previous):+.1%}）</strong>'
        else:
            comparison = f'昨日同期 0 人 · 今日 {today_total:,} 人'

        maximum = max(len(visitors[day]) for day in days)
        step = max(1, (maximum + 3) // 4)
        if step > 4:
            interval = 10 ** (len(str(step)) - 1)
            step = ((step + interval - 1) // interval) * interval
        axis = ''.join(f'<li>{step * tick:,}</li>' for tick in range(4, -1, -1))
        coverage = f'自 {since:%m/%d %H:%M} 开始采集。' if since.date() >= days[0] else ''
        rendered = Template(TEMPLATE.read_text(encoding='utf-8')).substitute(
            admin_path=escape(args.admin_path, quote=True),
            updated_label=f'更新于 {now:%m/%d %H:%M}（{escape(args.timezone)}）',
            today_iso=now.date().isoformat(),
            today_label=f'{now.month} 月 {now.day} 日',
            today_total=f'{today_total:,}',
            comparison=comparison,
            axis=axis,
            columns=columns_html(days, visitors, since, step * 4),
            date_range=f'{date_label(days[0])} – {date_label(days[-1])}',
            articles=articles_html(root, articles),
            coverage=coverage,
        )
        (stage / 'index.html').write_text(rendered, encoding='utf-8')
        details = stage / 'details.html'
        if log.stat().st_size:
            subprocess.run([
                'goaccess', str(log), '--log-format=COMBINED', '--anonymize-ip',
                '--ignore-panel=HOSTS', '--no-progress', '-o', str(details)
            ], check=True, env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'})
        else:
            details.write_text(
                '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>GoAccess 请求明细</title>'
                '<p>暂无请求记录。</p><a href="./">返回访问统计</a></html>', encoding='utf-8'
            )
        for filename in ('details.html', 'index.html'):
            output = stage / filename
            output.chmod(0o644)
            output.replace(args.output / filename)


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--log', type=Path, required=True)
    cli.add_argument('--output', type=Path, required=True)
    cli.add_argument('--public-root', type=Path, required=True)
    cli.add_argument('--since', required=True, help='日志开始时间，带时区的 ISO 8601')
    cli.add_argument('--timezone', default='Asia/Shanghai')
    cli.add_argument('--article-prefix', default='/posts/')
    cli.add_argument('--admin-path', default='/admin/')
    arguments = cli.parse_args()
    render(arguments, datetime.now(ZoneInfo(arguments.timezone)))
