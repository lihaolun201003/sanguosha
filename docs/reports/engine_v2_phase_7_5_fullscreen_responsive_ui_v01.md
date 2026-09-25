# Engine V2 Phase 7.5：Fullscreen / Resolution-Aware UI Layout

报告日期：2026-09-19
目标：让界面用上现代桌面屏幕的真实空间，而不是继续在 1000×700 里缩小字体、缩小图标、截断文字。
范围：只做显示层（屏幕尺寸、全屏、布局、间距、文本与图标排版、动画落点）。规则、Skill、General、Flow 语义未改动。

---

## 1. 原固定分辨率问题

Phase 6 的 UI 以 1000×700 为唯一画布，所有坐标都是写死的像素值，因此：

| 现象 | 根因 |
|---|---|
| 字体互相挤压、中文偏小 | 字号固定 15～26px，在 1000px 宽里没有放大余地 |
| 文字与图标重叠 | 装备槽把图标和文字画在同一个 rect 里；判定行超出面板底部 |
| 装备文字空间不足 | 面板 194px 宽，两列各 90px，还要塞图标 |
| SeatCard 内容过密 | 126px 高要塞头像、名字、HP、手牌数、两行装备、判定行 |
| 真人状态栏与手牌贴合 | 状态条底 574、手牌顶 578，仅 4px |
| Hover 遮挡状态信息 | 上浮 22px 会覆盖状态条下沿 |
| 中央区域不足 | 624×288 要同时放牌堆、弃牌堆、出牌位、公共牌区 |
| 部分标签过小 | micro 仅 13px |
| 换大屏没有收益 | 布局完全固定，屏幕再大也是中间一小块 |

本阶段的方向不是继续缩小，而是**增大有效空间 + 按真实分辨率重新布局**。

---

## 2. Fullscreen 实现

`main.py`：

```python
def _desktop_size():
    info = pygame.display.Info()
    return (info.current_w, info.current_h)   # 拿不到则退回窗口默认尺寸

def _set_mode(fullscreen):
    if fullscreen:
        try:
            return pygame.display.set_mode(_desktop_size(), pygame.FULLSCREEN), True
        except pygame.error:
            pass
    return pygame.display.set_mode(WINDOWED_SIZE, pygame.RESIZABLE), False
```

- **默认全屏**（需求第二条），分辨率来自 `pygame.display.Info()`，没有写死任何尺寸。
- 全屏失败（例如无显示设备 / dummy 驱动 / 分辨率不支持）自动退回窗口模式，不会崩。
- 切换后立即 `renderer.set_screen(screen)` + 各组件 `sync_layout(metrics)`，布局在下一帧重建。

---

## 3. Windowed 实现

- 默认窗口 **1280×800**（`src/constants.py` 的 `WINDOWED_SIZE`）。
- 使用 `pygame.RESIZABLE`，支持拖动窗口边缘。
- 监听 `pygame.VIDEORESIZE`：用新尺寸重建 `screen` 并刷新所有布局。
- 最小尺寸钳制在 640×480，避免极端拖拽导致布局无意义。
- 布局本身对宽高比不敏感：低于 16:9 的比例会被 letterbox 居中，不会崩，也不会拉伸内容。

---

## 4. F11

```python
if event.key == pygame.K_F11:
    apply_display_mode(not fullscreen_active)
elif event.key == pygame.K_ESCAPE and fullscreen_active:
    apply_display_mode(False)
```

`apply_display_mode()` 做四件事：

```text
重建 screen（FULLSCREEN / RESIZABLE）
renderer.set_screen(screen)      → 清空 layout 缓存，下一帧按新尺寸重建
choice_overlay.sync_layout(...)  → 弹窗重新居中
start_menu.sync_layout(...)      → 菜单重新居中
```

因此不会出现"窗口变大了但 UI 还缩在左上角"。

---

## 5. LayoutMetrics

`src/ui/layout.py` 现在是唯一几何来源：

```text
DESIGN_WIDTH / DESIGN_HEIGHT = 1600 × 900      ← 布局只按设计坐标书写
LayoutMetrics(width, height):
    scale      = min(w / 1600, h / 900)        ← 取更紧的一轴，保证内容完整
    offset_x/y = 居中留白（16:9 时为 0）
    px(v) / point(x, y) / rect(x, y, w, h) / to_screen(rect)
    fonts      = ScaledFonts(scale)            ← 字体按同一比例缩放
    central / prompt / player_status / hand_area /
    primary_button / secondary_button / speed_control / log_rect
    hand_card_size() / hand_top() / animation_rects()
```

关键点：

- **不是整体拉伸 Surface**：组件拿到的是换算后的 rect，然后按真实像素重新绘制文字与图形，所以字体清晰、命中精确。
- **命中与绘制同源**：`TableLayout` 内部只用 `metrics.rect(...)` 产生 rect，画的就是点得到的。
- **尺寸变化即失效**：`Renderer.set_screen()` 把 `_layout_size` 置空，下一帧 `refresh_layout()` 重建全部 rect。

设计坐标（1600×900）：

```text
顶部座位带     y 16..164      高 148，1/2/3 人分别为 330/310/286 宽
两侧座位       x 16 / 1336    252×148，纵向步进 164
中央区         (288,176) 1024×364
  牌堆          (340,300) 100×140        弃牌堆 (1160,300) 100×140
  出牌位        (722,292) 104×144        响应位 (846,292) 104×144
  公共牌区      y 186，80×112，间距 12
Prompt         (288,546) 1024×84
真人状态条     (288,638) 1024×62
手牌           y 742，单卡 106×148，可用宽 1024
主/次按钮      (1336,742) 248×60 / (1336,812) 248×48
节奏控件       (16,16) 240×84
战报           左下角 268×124
```

---

## 6. 主要区域变化

| 区域 | Phase 6（1000×700） | 现在（1600×900 设计） | 1080p 实际像素 |
|---|---|---|---|
| 画布 | 1000×700 | 1600×900 | 1920×1080 |
| AI 座位 | 194×132 / 178×132 | 286×148 / 252×148 | 343×178 / 302×178 |
| 中央区 | 624×288 | 1024×364 | 1229×437 |
| 手牌单卡 | 88×122 | 106×148 | 127×178 |
| 手牌可用宽 | 624 | 1024 | 1229 |
| 状态条 | 624×46 | 1024×62 | 1229×74 |
| Prompt | 624×60 | 1024×84 | 1229×101 |
| 按钮 | 170×56 / 170×44 | 248×60 / 248×48 | 298×72 / 298×58 |

间距（留白）也一并放大：状态条底到 手牌顶 的设计间距从 Phase 6 的 4px 变成 **104px**，是选中上浮（36px）的近三倍。

---

## 7. SeatCard 调整

- 头像从 38px 设计升到 **46px**，名字字号 `seat_name = 24`（1080p 下 29px）。
- **图标与文字分区**：每个装备槽先把左侧 18×18 交给图标，再在右侧剩余宽度里绘制文字，两者不再共用 rect。
- **文本按真实宽度省略**：名字、装备名都用 `ellipsize_text()`（逐字测量 `font.size`），放不下才补省略号，绝不按字符数硬切。
- 判定区标签行从 `y+134` 收到 `y+126`，并把文字垂直居中在 18px 高的标签带里，不再溢出面板底部。
- 座次仍是右上小徽章；状态徽章（横置 / 阵亡）固定右上角并预留空间，不与名字争位。
- 血点半径与间距随 scale 放大（1080p 下半径 8.4px、间距 19px），HP 数字与 "手牌 ×N" 分别左/右对齐，互不挤压。

---

## 8. 手牌调整

- 卡牌从 88×122 放大到 **106×148**（1080p 下 127×178），比例仍是 0.716。
- **放得下就完整展开**：`natural = count*(width+gap) - gap`，只要不超过手牌区宽就按 `width+12` 步进，牌之间真正留出间隙（修掉了 Phase 6 里"放得下也按 12px 重叠"的算法错误）。
- **放不下才重叠**：按 `(可用宽 − 牌宽) / (张数 − 1)` 压缩；30 张牌时步进仍有 40px（1080p 下 48px），花色与点数保持可见。
- 悬停上浮 24、选中上浮 36（设计值，随 scale 缩放）。
- 命中区仍是"基础矩形 ∪ 上浮矩形"，抬起后鼠标不会脱离。

---

## 9. Prompt 调整

- 面板从 624×60 加到 **1024×84**，文字分两层：标题一行（`normal`）、说明一行（`small`），进度（"已选择 1 / 2"、"还需选择 1 张"）右对齐在说明行。
- 说明文字按面板宽省略，不再与进度挤在同一行。
- 状态归一逻辑（响应 / 选牌 / 目标 / 等待 / 出牌 / 弃牌）未变，只改了排版。

---

## 10. 字体调整

设计字号整体上调，并按 `scale` 缩放：

| 名称 | Phase 6 | 现在（设计） | 1080p 实际 |
|---|---|---|---|
| hero | 68 | 72 | 86 |
| title | 52 | 56 | 67 |
| huge | 44 | 46 | 55 |
| large | 32 | 34 | 41 |
| normal | 24 | 26 | 31 |
| small | 19 | 21 | 25 |
| tiny | 15 | 17 | 20 |
| micro | 13 | 15 | 18 |
| card | 26 | 30 | 36 |
| card_small | 20 | 24 | 29 |
| seat_name | 20 | 24 | 29 |

- 字体缓存 key 从"名字"变成 `(名字, 实际像素尺寸)`，分辨率变化只是多几个尺寸，不会每帧新建字体，也不会无限增长。
- 1280×720 下 `normal` 是 21px、`micro` 是 12px，仍可读；2560×1440 下 `normal` 到 42px。

---

## 11. Tooltip 调整

- 提示框宽度改为 `min(px(560), 屏幕宽 × 0.42)`，4K 下不会出现一行 2000px 的超长文本。
- 行高按 `font.get_linesize() + px(4)` 计算，中文字体行距不再互相压盖。
- 换行沿用 `wrap_tooltip_text()`（逐字测量），"攻击范围"与"效果说明"天然分行。

---

## 12. 文本/图标重叠修复

| 问题 | 修复 |
|---|---|
| 装备图标与名称重叠 | 槽内先分图标 rect 再算文字 rect，中间留 6px 间隙 |
| "判定区空"溢出面板底部 | 判定行上移到 `y+126`，文字在 18px 标签带内垂直居中 |
| 长装备名被硬切 | 统一 `ellipsize_text()` 逐字测量 + 省略号 |
| 名字与状态徽章争位 | 名字可用宽度 = 面板宽 − 内边距 − 徽章预留（66px） |
| HP 数字与手牌数相撞 | 血点左对齐、手牌数右对齐，各占一端 |
| 手牌放得下也重叠 | 修正步进算法（见第 8 节） |
| Prompt 文字挤一行 | 标题/说明/进度分层 |
| 节奏控件标题与按钮重叠 | 控件内改为"标题居中 + 数值块 + 左右步进按钮"三段式 |

---

## 13. 动画坐标修复

Phase 6 的引擎动画落点直接读 `src/constants.py` 里的固定像素（`TABLE_CARD_RECT` 等），换分辨率后牌会飞到旧位置。现在：

```text
Renderer.begin_frame()
    → game.ui_rects   = table_layout.animation_rects()   # 当前分辨率的屏幕坐标
    → game.ui_metrics = metrics

GameEngine.animation_rect(key, fallback)
    → 优先读 game.ui_rects[key]，没有 UI（纯规则测试）时退回设计默认值
```

接入的动画：出牌飞向中央、响应牌飞向响应位、弃牌飞向弃牌堆、摸牌起点（真人手牌 / 对手手牌位）。

- 规则层不依赖 UI：`ui_rects` 为空时行为与之前一致（226 项规则测试全部通过即为证据）。
- `src/constants.py` 里剩下的 UI 坐标只作为"无 UI 环境"的兜底，正常游戏不再使用。

---

## 14. 分辨率测试

`tests/test_engine_v2_phase7_5_ui_layout.py` 覆盖 **1280×720 / 1366×768 / 1600×900 / 1920×1080 / 2560×1440** 五种分辨率：

- `scale` 与 `min(w/1600, h/900)` 一致（含 1366×768 这种非整比例）。
- 16:9 下没有 letterbox；非 16:9（1024×768、1680×1050、2560×1080）不崩溃且中央区仍在屏幕内。
- 字体随分辨率变大。
- 每种分辨率下 2～8 人：座位不越界、互不重叠、不压中央区；Prompt / 状态条 / 手牌 / 按钮互不冲突。
- 分辨率切换后：手牌 rect 变化、命中重新对齐、按钮 rect 重建、`hit_action` 用新 rect、动画落点跟随。

`tools/ui_audit.py` 另外对 5 种分辨率 × 7 种人数 × 3 种手牌量 = **105 个组合**做几何审计：**0 处问题**。

---

## 15. 2～8 人测试

- 布局测试遍历 `ai_count = 1..7`（总人数 2～8）× 5 种分辨率，全部无重叠、无越界。
- 其中包含"满装备"场景（武器 / 防具 / ±1 马 / 判定牌 / 横置）与 30 张手牌的极端情况。
- 8 人局在 1280×720（本阶段支持的最小常见分辨率）下依然成立：顶部 3 席 + 左右各 2 席，与中央区、Prompt、真人区互不干扰。

---

## 16. Overlap Audit

`tools/ui_audit.py` 检查项（对应需求第三十六条）：

```text
座位 vs 座位          座位 vs 中央区       座位 vs Prompt
Prompt vs 状态条      状态条 vs 手牌区     手牌上浮(24/36) vs 状态条
按钮 vs 手牌/Prompt   按钮 vs 座位         按钮 vs 屏幕边界
节奏控件 vs 座位/边界
装备槽文字空间是否足够容纳长装备名
状态条与手牌间距是否 ≥ 选中上浮高度
```

输出：`审计完成：105 组合，0 处问题`。

---

## 17. Snapshot

`tools/ui_snapshot.py` 现在默认以 **1920×1080** 出图（可传尺寸参数），生成：

```text
00_menu.png                 开始菜单（居中面板）
table_02_players.png        2 人局
table_05_players.png        5 人局
table_08_players.png        8 人局
hand_18_with_targeting.png  5 人局 + 18 张手牌 + 目标选择中
prompt_response.png         响应 Pending
public_pool.png             五谷公共牌选择
choice_modal.png            二选一弹窗
game_over.png               结算 Overlay
```

`tools/ui_smoke.py` 另在 1080p 下渲染并保存布局场景（含 `E_full_equipment` 满装备场景）。

---

## 18. 回归测试

```text
python -m compileall -q main.py src tests tools    → 通过
python -m unittest discover -s tests               → Ran 256 tests, OK
```

| 测试模块 | 用例数 |
|---|---|
| Phase 1～4 规则 | 48 |
| Phase 5 规则 / 多人 / UI | 14 / 38 / 9 |
| Phase 6 UI（含节奏） | 47 |
| Phase 7 Skill / General | 48 |
| legacy 1v1 | 22 |
| **Phase 7.5 分辨率与布局（新增）** | **30** |
| **合计** | **256** |

原有 226 项全部保持通过；被改动的旧测试只有三处，都是"按钮位置改由 LayoutMetrics 决定"带来的坐标来源变化（测试改为读取 `StartMenu` 自己的按钮 rect），断言本身未放宽。

多人长局回归：42 局无武将 + 42 局带武将（3 个标准武将按座次轮转），异常 0。

---

## 19. Pygame dummy

`tools/ui_smoke.py` 在 1920×1080 dummy 下：

```text
布局场景 A～F（含五谷 / 无懈 / 求桃 / 铁索 / 顺手牵羊选装备）  12 项全通过，面板不越界、不重叠
dummy 完整交互流程                                            12 步全通过
  菜单选 4 AI → 进入 5 人局 → 开局动画 → 鼠标悬停上浮 → 点手牌进目标选择
  → 取消选择 → 结束回合 → AI 自动接管 → Pending 响应 → 重新开始 → 返回主菜单
```

---

## 20. 已知问题

1. **超宽屏与 4:3 只保证不崩、不保证好看**：布局按 `min(w/1600, h/900)` 等比缩放并居中，超宽屏会左右留黑边，4:3 会上下留黑边。需求明确本阶段只优先 16:9。
2. **窗口拖动是"跳变式"适配**：`VIDEORESIZE` 到达时重建布局，没有连续平滑的过渡动画。
3. **中央区在 2～5 人局仍偏空**：牌堆与弃牌堆固定在两侧，人少时中间留白较多；出牌与判定横幅出现时会填充。
4. **`src/constants.py` 仍保留旧 UI 坐标**：它们现在只作为"没有 Renderer 时"的动画兜底，正常游戏不再使用；彻底删除需要再确认没有其它引用。
5. **AI 面板的"判定区空"文字贴近面板下沿**：已经不再溢出，但视觉上偏紧，可以再上移 4px。
6. **全屏切换会重建字体缓存中的新尺寸**：缓存是 `(名字, 尺寸)` 键控的，来回切换只会多出有限的几个尺寸；长时间反复切换不同窗口大小才会让缓存条目缓慢增长（每个条目是一个 `Font` 对象，影响可忽略）。
7. **未做分辨率设置界面**：需求第五十九条明确本阶段不做复杂设置页，F11 与启动默认全屏已覆盖主要场景。
