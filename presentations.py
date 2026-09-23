"""Editable slide layout and PowerPoint export for analytical reports."""

import io
import math
import re
import zipfile

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.dml import MSO_COLOR_TYPE, MSO_FILL_TYPE
from pptx.enum.text import MSO_AUTO_SIZE, PP_ALIGN
from pptx.exc import PackageNotFoundError
from pptx.util import Inches, Pt


DEFAULT_TITLE = {"font": "Aptos", "size": 28, "bold": True, "color": "#14527c"}
DEFAULT_BODY = {"font": "Aptos", "size": 17, "bold": False, "color": "#193a54"}
DEFAULT_LAYOUT = {"title": {"x": 6, "y": 8, "w": 88, "h": 20},
                  "body": {"x": 8, "y": 33, "w": 84, "h": 57}}


def hex_color(color):
    if not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        raise ValueError("Некорректный цвет")
    return color.lower()


def validate_style(style):
    if (not isinstance(style, dict) or not isinstance(style.get("font"), str) or
        not 1 <= len(style["font"]) <= 80 or any(ord(char) < 32 for char in style["font"]) or
        type(style.get("size")) not in (int, float) or not math.isfinite(style["size"]) or
        not 8 <= style["size"] <= 64 or type(style.get("bold")) is not bool):
        raise ValueError("Некорректные параметры шрифта")
    return {"font": style["font"], "size": style["size"], "bold": style["bold"],
            "color": hex_color(style.get("color"))}


def validate_colors(colors):
    if not isinstance(colors, dict) or any(not isinstance(colors.get(key), str) or
                                          not re.fullmatch(r"#[0-9a-fA-F]{6}", colors[key])
                                          for key in ("background", "text", "accent")):
        raise ValueError("Выберите цвета фона, текста и акцента")
    return {key: colors[key].lower() for key in ("background", "text", "accent")}


def validate_deck(deck):
    if not isinstance(deck, dict) or not isinstance(deck.get("slides"), list) or not 3 <= len(deck["slides"]) <= 20:
        raise ValueError("Презентация должна содержать от 3 до 20 слайдов")
    cleaned = {"colors": validate_colors(deck.get("colors")), "slides": []}
    for slide in deck["slides"]:
        if not isinstance(slide, dict) or not isinstance(slide.get("elements"), list) or not 1 <= len(slide["elements"]) <= 8:
            raise ValueError("Каждый слайд должен содержать от 1 до 8 текстовых объектов")
        elements = []
        for element in slide["elements"]:
            if not isinstance(element, dict) or element.get("kind") not in ("title", "body") or not isinstance(element.get("text"), str) or not element["text"].strip() or len(element["text"]) > 1200:
                raise ValueError("Некорректный текст слайда")
            coordinates = [element.get(key) for key in ("x", "y", "w", "h")]
            if any(type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 100 for value in coordinates):
                raise ValueError("Некорректные координаты объекта")
            x, y, w, h = coordinates
            if w < 5 or h < 5 or x + w > 100 or y + h > 100:
                raise ValueError("Объект должен помещаться на слайде")
            cleaned_element = {"kind": element["kind"], "text": element["text"].strip(),
                               "x": x, "y": y, "w": w, "h": h}
            if "style" in element:
                cleaned_element["style"] = validate_style(element["style"])
            elements.append(cleaned_element)
        cleaned_slide = {"elements": elements}
        if "background" in slide:
            cleaned_slide["background"] = hex_color(slide["background"])
        cleaned["slides"].append(cleaned_slide)
    return cleaned


def _solid_color(fill):
    try:
        if fill.type == MSO_FILL_TYPE.SOLID and fill.fore_color.type == MSO_COLOR_TYPE.RGB:
            return "#" + str(fill.fore_color.rgb).lower()
    except (AttributeError, ValueError):
        pass
    return None


def _text_style(shape, default):
    for paragraph in shape.text_frame.paragraphs:
        for run in paragraph.runs:
            if not run.text.strip():
                continue
            font = run.font
            try:
                color = "#" + str(font.color.rgb).lower() if font.color.type == MSO_COLOR_TYPE.RGB else None
            except (AttributeError, ValueError):
                color = None
            return validate_style({"font": font.name or default["font"],
                                   "size": round(font.size.pt, 1) if font.size else default["size"],
                                   "bold": font.bold if font.bold is not None else default["bold"],
                                   "color": color or default["color"]})
    return default.copy()


def _shape_box(shape, width, height, fallback):
    x = max(0, round(shape.left / width * 100, 1))
    y = max(0, round(shape.top / height * 100, 1))
    w = min(100 - x, round(shape.width / width * 100, 1))
    h = min(100 - y, round(shape.height / height * 100, 1))
    return {"x": x, "y": y, "w": w, "h": h} if w >= 5 and h >= 5 else fallback.copy()


def extract_template_style(content):
    """Extract visual properties only; never keep or forward reference slide text."""
    if len(content) > 5 * 1024 * 1024 or not zipfile.is_zipfile(io.BytesIO(content)):
        raise ValueError("Загрузите презентацию PPTX размером до 5 МБ")
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if len(archive.infolist()) > 2000 or sum(item.file_size for item in archive.infolist()) > 40 * 1024 * 1024:
                raise ValueError("Распакованный PPTX слишком большой")
        presentation = Presentation(io.BytesIO(content))
        if not 1 <= len(presentation.slides) <= 50:
            raise ValueError("В образце должно быть от 1 до 50 слайдов")
        slides = list(presentation.slides)
        if any(len(slide.shapes) > 300 for slide in slides):
            raise ValueError("Слишком много объектов в образце")
        content_slide = next((slide for slide in slides if sum(shape.has_text_frame and bool(shape.text.strip())
                             for shape in slide.shapes) >= 2), slides[0])
        cover_slide = slides[0]
        def pick(slide):
            shapes = [shape for shape in slide.shapes if shape.has_text_frame and shape.text.strip()]
            if not shapes:
                return None, None
            title = max(shapes, key=lambda shape: (_text_style(shape, DEFAULT_TITLE)["size"], -shape.top))
            body = max((shape for shape in shapes if shape != title), key=lambda shape: len(shape.text), default=None)
            return title, body
        cover_title, _ = pick(cover_slide)
        title, body = pick(content_slide)
        if not cover_title and not title:
            raise ValueError("Добавьте в образец хотя бы один слайд с текстом")
        title = title or cover_title
        cover_title = cover_title or title
        title_style = _text_style(title, DEFAULT_TITLE)
        body_style = _text_style(body, DEFAULT_BODY) if body else DEFAULT_BODY.copy()
        cover_style = _text_style(cover_title, title_style)
        content_background = _solid_color(content_slide.background.fill) or "#ffffff"
        cover_background = _solid_color(cover_slide.background.fill) or content_background
        def layout(title_shape, body_shape):
            return {"title": _shape_box(title_shape, presentation.slide_width, presentation.slide_height,
                                          DEFAULT_LAYOUT["title"]),
                    "body": _shape_box(body_shape, presentation.slide_width, presentation.slide_height,
                                         DEFAULT_LAYOUT["body"]) if body_shape else DEFAULT_LAYOUT["body"].copy()}
        return {"colors": {"background": content_background, "text": body_style["color"],
                            "accent": title_style["color"]},
                "cover": {"background": cover_background, "layout": layout(cover_title, None),
                          "title": cover_style, "body": body_style},
                "content": {"background": content_background, "layout": layout(title, body),
                            "title": title_style, "body": body_style}}
    except (ValueError, KeyError, TypeError, PackageNotFoundError, zipfile.BadZipFile, OSError) as exc:
        if isinstance(exc, ValueError) and str(exc) in ("Распакованный PPTX слишком большой",
                                                        "В образце должно быть от 1 до 50 слайдов",
                                                        "Слишком много объектов в образце",
                                                        "Добавьте в образец хотя бы один слайд с текстом"):
            raise
        raise ValueError("Не удалось прочитать оформление PPTX") from exc


def deck_from_outline(outline, count, colors, template=None):
    if not isinstance(outline, dict) or not isinstance(outline.get("slides"), list) or len(outline["slides"]) != count:
        raise ValueError("ИИ вернул неверное количество слайдов")
    slides = []
    for index, item in enumerate(outline["slides"]):
        if not isinstance(item, dict) or not isinstance(item.get("title"), str) or not isinstance(item.get("bullets"), list):
            raise ValueError("ИИ вернул некорректную структуру слайдов")
        title = item["title"].strip()[:180]
        bullets = item["bullets"]
        if not title or len(bullets) > 6 or any(not isinstance(bullet, str) or not bullet.strip() or len(bullet) > 180 for bullet in bullets):
            raise ValueError("ИИ вернул слишком длинный или пустой текст слайда")
        body = "\n".join("• " + bullet.strip() for bullet in bullets)
        sample = template["cover" if index == 0 else "content"] if template else None
        title_box = sample["layout"]["title"] if sample else DEFAULT_LAYOUT["title"]
        body_box = sample["layout"]["body"] if sample else DEFAULT_LAYOUT["body"]
        elements = [{"kind": "title", "text": title, **title_box}]
        if sample:
            elements[0]["style"] = {**sample["title"], "color": sample["title"]["color"] if index == 0 else colors["accent"]}
        if body:
            element = {"kind": "body", "text": body, **body_box}
            if sample:
                element["style"] = {**sample["body"], "color": colors["text"]}
            elements.append(element)
        slide = {"elements": elements}
        if sample:
            slide["background"] = sample["background"] if index == 0 else colors["background"]
        slides.append(slide)
    return validate_deck({"colors": colors, "slides": slides})


def pptx_bytes(deck):
    deck = validate_deck(deck)
    presentation = Presentation()
    presentation.slide_width = Inches(13.333)
    presentation.slide_height = Inches(7.5)
    colors = {key: RGBColor.from_string(value[1:]) for key, value in deck["colors"].items()}
    for item in deck["slides"]:
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        slide.background.fill.solid()
        slide.background.fill.fore_color.rgb = RGBColor.from_string(item.get("background", deck["colors"]["background"])[1:])
        for element in item["elements"]:
            shape = slide.shapes.add_textbox(int(presentation.slide_width * element["x"] / 100),
                                             int(presentation.slide_height * element["y"] / 100),
                                             int(presentation.slide_width * element["w"] / 100),
                                             int(presentation.slide_height * element["h"] / 100))
            frame = shape.text_frame
            frame.word_wrap = True
            frame.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
            frame.margin_left = frame.margin_right = Inches(.06)
            frame.margin_top = frame.margin_bottom = Inches(.04)
            for line_number, line in enumerate(element["text"].splitlines()):
                paragraph = frame.paragraphs[0] if line_number == 0 else frame.add_paragraph()
                paragraph.text = line
                paragraph.alignment = PP_ALIGN.LEFT
                paragraph.space_after = Pt(9)
                for run in paragraph.runs:
                    style = element.get("style") or (DEFAULT_TITLE if element["kind"] == "title" else DEFAULT_BODY)
                    run.font.name = style["font"]
                    run.font.size = Pt(style["size"])
                    run.font.bold = style["bold"]
                    run.font.color.rgb = (RGBColor.from_string(style["color"][1:]) if element.get("style")
                                          else colors["accent" if element["kind"] == "title" else "text"])
    output = io.BytesIO()
    presentation.save(output)
    return output.getvalue()
