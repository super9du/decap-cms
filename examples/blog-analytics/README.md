# 博客访问统计

在 Decap CMS 的站点配置中启用入口：

```yaml
analytics:
  path: /admin/analytics/
  label: 访问统计
  # 可选，CMS 退出时 POST 此站内路径以清除报表会话。
  logout_path: /admin/analytics/session/logout
```

`analytics.path` 是站内路径，以斜杠开头和结尾。实际报表由部署提供，Decap 只在已登录界面的顶部显示入口。报表嵌在后台导航中，使用 `#embedded` 隐藏独立页面的顶部。应在服务端对报表目录及每个文件校验管理员会话，并允许同源页面嵌入看板。把报表输出放在站点公开目录之外，通过受保护的路径提供。

在 Nginx 的 HTTP 上下文声明以下格式，并在需要一起统计的站点写入同一份报表日志：

```nginx
map $request_uri $analytics_path {
    "~^([^?]+)" $1;
    default /;
}
log_format blog_analytics '$host $remote_addr - - [$time_local] '
                          '"$request_method $analytics_path $server_protocol" '
                          '$status $body_bytes_sent "-" "$http_user_agent"';
# 站点的 server 块内
# access_log /var/log/nginx/blog.analytics.log blog_analytics;
```

将日志和轮转后的文件留在服务器。安装 Python 3.10+ 和 GoAccess 后，可定时执行：

```sh
python3 render.py --log /var/log/nginx/blog.analytics.log \
  --output /srv/blog-analytics/reports --public-root /srv/blog/public \
  --since '2026-01-01T00:00:00+08:00'
```

将 `--since` 换成实际日志开始时间，设置日志轮转和报表更新任务。统计页按成功页面请求估算按日独立访客，按文章前缀统计热门内容。请求明细由 GoAccess 输出，报告中关闭主机列表、对 IP 匿名化，并且源日志格式省略查询参数及来源。域名、认证配置、真实日志和生成的报表均在部署环境提供，示例代码无需这些数据。

支持图文文章目录的 Hugo 站点可在文章集合下配置 `entry_depth: 2`，同时扫描直接放在集合目录的 Markdown 和子目录下的 Markdown。该配置只影响读取深度。

简体中文明细可在服务器安装对应的 GoAccess 语言包与系统 locale 后，给 `render.py` 添加 `--goaccess-locale zh_CN.UTF-8`。Nginx 日志中的英文月份仍按英文解析。

明细使用 GoAccess 原生报表。`--ignore-crawlers` 对整份报表排除已识别爬虫，`--http-protocol=no` 将同一文章不同 HTTP 版本的请求合并。「虚拟主机」面板根据日志中的域名显示请求数、独立访客和传输量。日志格式升级前的记录可通过 `--legacy-host` 归入原站点。

为原生 HTML 开启日期筛选时，定时生成器使用 `--range-snapshot /受保护目录/range.log` 额外写入最多 30 天的日志快照。日期服务运行 `range_report.py --snapshot ... --locale zh_CN.UTF-8`，通过 systemd 传入的 Unix 监听套接字接收请求。在 Nginx 验证管理员会话后，将报表地址和查询参数代理到该套接字。快照包含用于去重的访客字段，应放在公开目录外，只给生成器以及统计服务读权限。

日期服务筛选快照后调用原来的 GoAccess 二进制生成完整报表。顶部控件适配 GoAccess 的浅色和深色主题，并通过报表的原生渲染顺序将虚拟主机放在第一排右侧。

需要在公开文章页显示累计阅读量时，为定时生成器添加 `--public-article-views /站点可读取目录/article-views.json`。生成器用当前 sitemap 确认已发布文章，只统计 `--legacy-host` 对应站点的成功页面请求并过滤已识别机器人。首次使用仍可读取的日志，之后逐日保留已累计的计数。每日历史文件留在受保护的 `--output` 目录，公开 JSON 只包含文章路径及浏览次数。Nginx 可为 `/article-views.json` 配置单文件别名并关闭其访问日志，供文章页获取。
