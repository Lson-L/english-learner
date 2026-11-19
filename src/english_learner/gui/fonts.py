"""应用字体：内置开源英文字体 + 中文回退链。

设计原则（后续开发请遵守）：
- **整个应用的英文外观只由这里决定**。字体链写进全局 QSS 的 `*` 规则，
  新加的页面/控件默认就生效，不要在各个控件上单独设 font-family；
- 唯一的例外是文章正文（QTextDocument）：它不归 QSS 管，必须调
  `ArticleView.set_english_font(key)`；
- 中文字形一律交给系统雅黑（这些英文字体都不含 CJK，Qt 会逐字回退）。

可选字体（均为 SIL Open Font License 1.1，见 assets/fonts/OFL-*.txt）：
- `Lexend`（默认）：无衬线，字形通透，长文与界面都舒服；
- `Literata`：衬线，为屏幕长文阅读设计；
- `Georgia`：系统自带，不打包，零体积；
- `Inter`：为界面小字号设计，信息密度最高。

字体文件来自 npm 包 `@expo-google-fonts/*`（Google Fonts / GitHub 在本机网络不可达，
只能走 npmmirror 取 TTF）。

Java 对照：本模块 ≈ 应用启动时注册自定义字体的配置类，`load_application_fonts()`
相当于往 `GraphicsEnvironment.registerFont()` 里注册一遍，之后按族名引用即可。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtGui import QFontDatabase

logger = logging.getLogger(__name__)

# assets 与 gui 同级，都在 english_learner 包内。
FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"

# 中文回退：Windows 10 自带雅黑。字体链里必须保留，否则中文会变成方块。
CJK_FALLBACK_FAMILIES = ("Microsoft YaHei UI", "Microsoft YaHei")


@dataclass(frozen=True)
class FontChoice:
    """一种可选的英文字体。

    key     —— 存进 config.json 的稳定标识，改它会丢掉用户已有选择，不要乱动；
    label   —— 设置页下拉框里的说明文字；
    family  —— Qt 里的英文字体族名，必须和 TTF 内部的族名一致；
    generic —— QSS 末尾的通用族名兜底（sans-serif / serif）。
    """

    key: str
    label: str
    family: str
    generic: str


# 顺序即下拉框顺序，第一项是默认值。
ENGLISH_FONT_CHOICES: tuple[FontChoice, ...] = (
    FontChoice("lexend", "Lexend（无衬线，默认）", "Lexend", "sans-serif"),
    FontChoice("literata", "Literata（衬线）", "Literata", "serif"),
    FontChoice("georgia", "Georgia（系统自带）", "Georgia", "serif"),
    FontChoice("inter", "Inter（界面风）", "Inter", "sans-serif"),
)

DEFAULT_ENGLISH_FONT = ENGLISH_FONT_CHOICES[0].key
_CHOICES_BY_KEY = {choice.key: choice for choice in ENGLISH_FONT_CHOICES}


def get_english_font(key: str | None) -> FontChoice:
    """按 key 取字体选项；空值或未知 key 一律回退默认，坏配置不影响启动。"""
    return _CHOICES_BY_KEY.get((key or "").strip().lower(), _CHOICES_BY_KEY[DEFAULT_ENGLISH_FONT])


def english_font_families(key: str | None = None) -> list[str]:
    """`QFont.setFamilies()` 用的族名列表：英文字体 + 中文回退。"""
    choice = get_english_font(key)
    return [choice.family, *CJK_FALLBACK_FAMILIES]


def english_font_stack(key: str | None = None) -> str:
    """`QSS font-family` 用的完整字体链（英文 → 雅黑 → 通用族名）。"""
    choice = get_english_font(key)
    fallbacks = ", ".join(f'"{family}"' for family in CJK_FALLBACK_FAMILIES)
    return f'"{choice.family}", {fallbacks}, {choice.generic}'


def load_application_fonts() -> tuple[str, ...]:
    """把 `assets/fonts/*.ttf` 注册进 Qt，返回成功注册的字体族名。

    必须在 QApplication 创建之后、应用样式表之前调用。单个文件加载失败只记
    warning，界面会自动回退到系统字体，不影响启动。
    """
    families: list[str] = []
    for path in sorted(FONT_DIR.glob("*.ttf")):
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id == -1:
            logger.warning("字体注册失败：%s（将回退到系统字体）", path.name)
            continue
        families.extend(QFontDatabase.applicationFontFamilies(font_id))

    if not families:
        logger.warning("没有注册到任何内置字体，字体目录：%s", FONT_DIR)
    else:
        logger.info("已注册内置字体：%s", "、".join(dict.fromkeys(families)))
    return tuple(dict.fromkeys(families))
