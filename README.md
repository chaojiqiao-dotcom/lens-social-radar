# Lens Social Radar v1.2

美瞳 / 眼睛类社媒周报仪表盘。每周一统计上一周 **周二至周日** 的 Instagram、TikTok 内容，并输出：

- Instagram TOP 10
- TikTok TOP 10
- 三个平台归一化后的综合总榜 TOP 3 + 深度分析
- 东南亚 / 欧美 / 中东地区信号
- 内容类型趋势

## v1.2 本次更新

1. 首页新增“社媒案例库”全局搜索。
2. 可搜索品牌、账号、平台、地区、美瞳颜色、画面元素、拍摄方式、剪辑手法、Hook、内容形式等关键词。
3. 支持平台二次筛选：全部 / Instagram / TikTok。
4. 支持快捷搜索词，例如：眼部特写、大脸近景、单眼对比、自然光、Jump Cut、卡点、GRWM、产品平铺。
5. 搜索结果按“关键词相关度 + 内容综合分”排序，并显示匹配创意标签。
6. 每周更新时同步维护 `docs/data/archive.json` 历史案例索引；真实采集模式会持续累积并去重，DEMO 模式只保留当前演示周，避免生成假历史。
7. 数据模型增加 `search_tags`、`visual_tags`、`shooting_tags`、`editing_tags`、`hook_tags`、`hashtags`，为后续视觉 AI 自动标注和详情分析页做准备。
8. 保留 v1.1 的 Instagram / TikTok TOP 10、跨平台 TOP 3、封面缓存和 TikTok 展示兼容逻辑。

## 快速预览（完全不需要代码）

解压后进入：

```text
docs/preview.html
```

直接双击即可。预览版为 30 条合成 DEMO 数据，每个平台 10 条。DEMO 封面只是占位图；接入真实数据后会显示真实帖子的首帧 / 封面。首页搜索在 DEMO 中也可直接测试，例如搜索“自然光”“Jump Cut”“眼部特写”。

## 本地运行

在项目根目录打开 CMD：

```bash
python scripts/weekly_update.py --mode demo
python -m http.server 8000 -d docs
```

然后浏览器打开：

```text
http://localhost:8000
```

Windows 如果 `python` 无法识别，可换成 `py`。

## 榜单评分方式

先在每个平台内部做百分位归一化，再进行跨平台比较：

- 触达 / 播放：35%
- 互动率：25%
- 分享 + 收藏意图：20%
- 增长速度：10%
- 美瞳 / 眼睛内容相关性：10%

因此 TikTok 不会仅仅因为播放量绝对值更大就自动压过 Instagram / 。

## 推荐真实数据格式

放入 `data/incoming/*.json` 后，可运行 `incoming` 模式。每条帖子建议至少包含：

```json
{
  "platform": "tiktok",
  "url": "https://www.tiktok.com/@creator/video/1234567890",
  "mobile_url": "https://www.tiktok.com/@creator/video/1234567890",
  "video_id": "1234567890",
  "author": "creator",
  "brand": "Brand",
  "region": "Southeast Asia",
  "content_type": "video",
  "caption": "...",
  "posted_at": "2026-10-02T12:00:00Z",
  "views": 100000,
  "likes": 12000,
  "comments": 300,
  "shares": 1000,
  "saves": 1500,
  "followers": 250000,
  "thumbnail": "https://.../cover.jpg",
  "relevance_score": 90,
  "media_notes": "Eye macro close-up, one-eye comparison, cool daylight",
  "search_tags": ["单眼对比", "眼部特写", "自然光"],
  "shooting_tags": ["微距", "自然光"],
  "editing_tags": ["Jump Cut", "卡点"],
  "hook_tags": ["单眼对比", "强Hook"]
}
```

常见第三方数据字段（如 `share_url`、`cover_image_url`、`thumbnail_url`、`playCount`、`diggCount` 等）已经做了基础字段映射。

## TikTok：手机能看、电脑网页不能看怎么办？

工具将“数据采集”和“桌面网页播放”分开处理。

### 1. 排名不依赖桌面端能否播放

只要数据源能合法提供公开内容的帖子 ID、作者、发布时间、播放 / 点赞 / 评论 / 分享 / 收藏等指标，该视频就可以进入排名。TikTok Research API 的公开视频数据模型本身就包含这些指标字段。

### 2. 封面单独保存

如果采集源给到 `cover_image_url` / `thumbnail_url`，周报生成时会尽量把榜单需要展示的封面下载到 `docs/media/`，网页显示本地副本，而不是长期依赖临时 CDN 链接。

### 3. 缺封面时尝试 TikTok oEmbed

对于有 TikTok 原帖 URL、但采集器没给封面的公开帖子，工具会尝试 TikTok 官方 oEmbed 获取 `thumbnail_url`。如果视频受到登录、年龄、地区、隐私或删除限制，则不会尝试绕过限制。

### 4. 点击封面保留原始 TikTok 链接

TikTok 的分享链接在手机和电脑上的行为可能不同。手机打开时通常可以继续进入 TikTok App；桌面端若 TikTok 不允许播放，网页会保留该帖子在榜单中的指标与封面。

### 5. 如果数据供应商合法提供本地视频文件

可以额外把路径写入 `local_video`。后续可扩展为：桌面端原帖打不开时，使用本地副本抽首帧、做视觉分析。不要通过此工具绕过私密、年龄、地区、账号登录等访问控制。

## 三种采集模式

### `demo`

只用于界面测试：

```bash
python scripts/weekly_update.py --mode demo
```

### `incoming`

将合规数据供应商导出的 JSON 放入：

```text
data/incoming/
```

运行：

```bash
python scripts/weekly_update.py --mode incoming
```

### `apify`

保留通用 Apify Adapter。需要设置 `APIFY_TOKEN` 和对应 Actor。项目没有写死某个 Actor，因为 Actor、字段结构、访问能力和平台规则都可能发生变化。

## AI 分析

设置 OpenAI API Key 后，可自动生成综合 TOP 3 深度分析：

```bash
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5.6-luna
```

没有 Key 也可以运行，系统会使用基础规则生成分析。

## 重要的数据访问边界

不存在一个官方接口，可以让商业工具无条件抓取 Instagram、TikTok 的全部公开内容。生产环境应使用有授权 / 合规的数据源，并遵守平台条款与适用法律。

对于 TikTok，工具的 fallback 逻辑只帮助处理公开数据的封面、链接和展示兼容问题，不用于绕过私密内容、登录墙、年龄限制、地区限制或其他访问控制。

## 部署

1. 把整个项目上传到 GitHub Repository。
2. GitHub Pages 指向 `/docs`。
3. 在 GitHub Secrets 中配置采集器和可选的 OpenAI Key。
4. 把 `COLLECTION_MODE` 从 `demo` 改为 `incoming` 或 `apify`。
5. 手动运行一次 Workflow 验证，然后让每周任务自动执行。

GitHub Actions 配置在：

```text
.github/workflows/weekly-update.yml
```

