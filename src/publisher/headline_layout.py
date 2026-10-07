"""One font-verified pixel layout for both PNG and MP4 outputs."""
import math
from pathlib import Path
import unicodedata

from fontTools.ttLib import TTFont
from fontTools.pens.boundsPen import BoundsPen

from src.publisher.headline_tools import RenderError, sha256
from src.publisher.headlines import validate_spec


def layout(text, spec, display, fonts, workspace):
    spec = validate_spec(spec)
    path = Path(fonts.get(spec['font_ref'], ''))
    if not path.is_file() or sha256(path) != spec['font_sha256']:
        raise RenderError('HEADLINE_FONT_UNAVAILABLE')
    try:
        font = TTFont(path, fontNumber=spec['font_face_index'], lazy=False)
        if path.read_bytes()[:4] != b'ttcf' and spec['font_face_index'] != 0:
            raise RenderError('HEADLINE_FONT_FACE_INVALID')
    except Exception as exc:
        raise RenderError('HEADLINE_FONT_FACE_INVALID') from exc
    try:
        if 'fvar' in font or font['OS/2'].usWeightClass != spec['font_weight']:
            raise RenderError('HEADLINE_FONT_WEIGHT_UNSUPPORTED')
        cmap = font.getBestCmap()
        for char in text:
            if char == '\n':
                continue
            if ord(char) not in cmap or unicodedata.category(char).startswith('C'):
                raise RenderError('HEADLINE_UNSUPPORTED_GLYPH')
            if unicodedata.combining(char) or unicodedata.bidirectional(char) in ('R', 'AL', 'AN'):
                raise RenderError('HEADLINE_SHAPING_UNSUPPORTED')
        width, height = display
        size = max(1, round(height * spec['font_size_ratio']))
        scale = size / font['head'].unitsPerEm
        ascent = math.ceil(font['hhea'].ascent * scale)
        descent = math.ceil(-font['hhea'].descent * scale)
        line_height = ascent + descent + math.ceil(max(0, font['hhea'].lineGap) * scale)
        glyphs = font.getGlyphSet()
        kerning = {}
        if 'kern' in font:
            for table in font['kern'].kernTables:
                if getattr(table, 'coverage', 0) & 1:
                    kerning.update(getattr(table, 'kernTable', {}))
        overhang = 0

        def measure(value):
            nonlocal overhang
            # Rounded advances/outline overhangs conservatively bound this simple
            # left-to-right layout. No platform font fallback is allowed.
            pen_x, left, right, previous = 0, 0, 0, None
            for char in value:
                name = cmap[ord(char)]
                pen_x += math.ceil(max(0, kerning.get((previous, name), 0)) * scale)
                pen = BoundsPen(glyphs)
                glyphs[name].draw(pen)
                if pen.bounds:
                    x0, y0, x1, y1 = pen.bounds
                    if y1 * scale > ascent or -y0 * scale > descent:
                        raise RenderError('HEADLINE_FONT_METRICS_UNSUPPORTED')
                    left = min(left, pen_x + math.floor(x0 * scale))
                    right = max(right, pen_x + math.ceil(x1 * scale))
                    overhang = max(overhang, math.ceil(-x0 * scale),
                                   math.ceil((x1-font['hmtx'][name][0]) * scale))
                pen_x += math.ceil(font['hmtx'][name][0] * scale)
                previous = name
            return max(right, pen_x) - left + 2

        limit = math.floor(width * spec['max_width'])
        lines = []
        for paragraph in text.split('\n'):
            if spec['wrapping'] == 'explicit' or measure(paragraph) <= limit:
                lines.append(paragraph)
                continue
            # Preserve whitespace exactly; break only an existing space into newline.
            remainder = paragraph
            while measure(remainder) > limit:
                candidates = [i for i, char in enumerate(remainder) if char == ' ' and i > 0 and measure(remainder[:i]) <= limit]
                if not candidates:
                    raise RenderError('HEADLINE_LAYOUT_OVERFLOW')
                split = candidates[-1]
                lines.append(remainder[:split])
                remainder = remainder[split + 1:]
            lines.append(remainder)
        if len(lines) > spec['max_lines']:
            raise RenderError('HEADLINE_MAX_LINES_EXCEEDED')
        widths = [measure(line) for line in lines]
        if max(widths) > limit:
            raise RenderError('HEADLINE_LAYOUT_OVERFLOW')
        box_w, box_h = max(widths), line_height * len(lines)
        if box_h > height * spec['max_block_height_ratio']:
            raise RenderError('HEADLINE_MAX_BLOCK_HEIGHT_EXCEEDED')
        anchor, top = round(width * spec['x']), round(height * spec['y'])
        left = anchor - {'left': 0, 'center': box_w / 2, 'right': box_w}[spec['alignment']]
        padding = math.ceil(height * spec['background_padding_ratio']) if spec['background_color'] else 0
        stroke = math.ceil(height * spec['stroke_width_ratio']) if spec['stroke_color'] else 0
        blur = height * spec['shadow_blur_ratio'] if spec['shadow_color'] else 0
        dx = round(height * spec['shadow_offset_x']) if spec['shadow_color'] else 0
        dy = round(height * spec['shadow_offset_y']) if spec['shadow_color'] else 0
        # Gaussian tail bounded at 4 sigma; reserve this plus outline and background.
        extent = max(padding, stroke, math.ceil(4 * blur) + abs(dx), math.ceil(4 * blur) + abs(dy)) + overhang + 2
        if (left - extent < width * spec['margin_x'] or left + box_w + extent > width * (1-spec['margin_x']) or
                top - extent < height * spec['margin_y'] or top + box_h + extent > height * min(1-spec['margin_y'],spec['safe_bottom_ratio'])):
            raise RenderError('HEADLINE_SAFE_MARGIN_OVERFLOW')
        # A verified local face is materialized only into the private temporary workspace.
        font.save(Path(workspace) / 'font.ttf')
        if sha256(path) != spec['font_sha256']:
            raise RenderError('HEADLINE_FONT_UNAVAILABLE')
        return dict(lines=lines, font_size=size, ascent=ascent, descent=descent, line_height=line_height,
                    widths=widths, box=[left, top, box_w, box_h], anchor=anchor, padding=padding,
                    stroke=stroke, blur=blur, shadow_offset=[dx, dy], spec=spec, display=display)
    finally:
        font.close()


def filters(plan, workspace):
    spec = plan['spec']
    left, top, width, height = plan['box']
    for index, line in enumerate(plan['lines']):
        (Path(workspace) / f'line{index}.txt').write_text(line, encoding='utf-8')

    def text_filters(color, shadow=False):
        result = []
        dx, dy = plan['shadow_offset'] if shadow else (0, 0)
        alignment = {'left': '0', 'center': 'text_w/2', 'right': 'text_w'}[spec['alignment']]
        for index, line in enumerate(plan['lines']):
            if not line:
                continue
            y = top + plan['ascent'] + index * plan['line_height'] + dy
            result.append(f"drawtext=fontfile=font.ttf:textfile=line{index}.txt:expansion=none:text_shaping=0:"
                          f"fontsize={plan['font_size']}:fontcolor={color}:x={plan['anchor'] + dx}-{alignment}:"
                          f"y={y}-ascent:borderw={0 if shadow else plan['stroke']}:"
                          f"bordercolor={spec['stroke_color'] or '#000000'}")
        return ','.join(result)

    foreground = text_filters(spec['text_color'])
    if spec['background_color']:
        pad = plan['padding']
        foreground = (f"drawbox=x={left-pad}:y={top-pad}:w={width+2*pad}:h={height+2*pad}:"
                      f"color={spec['background_color']}:t=fill," + foreground)
    if spec['shadow_color']:
        shadow = text_filters(spec['shadow_color'], True)
        blur = f",gblur=sigma={plan['blur']}" if plan['blur'] else ''
        return (f"[0:v]split[base][layer];[layer]format=rgba,colorchannelmixer=aa=0,{shadow}{blur}[shadow];"
                f"[base][shadow]overlay=shortest=1,{foreground}[out]")
    return f'[0:v]{foreground}[out]'
