"""image_fill: реальные байты картинок -> media-парт за a:blip/r:embed.

ContentPack хранит только метаданные ассетов — байты перечитываются из
исходного content-файла по artifact_id (markdown путь / docx#inner /
url). Замена байтов сохраняет content-type либо добавляет честный
Override, когда формат по сигнатуре отличается от заявленного.
"""

from __future__ import annotations

import json
import urllib.request
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from deckdna.contracts.content_pack import Asset, Kind1
from deckdna.pptx.composing.image_fill import (
    _MAX_REMOTE_IMAGE_BYTES,
    ResolvedImage,
    _fetch_public_image,
    _NoRedirect,
    detect_image_content_type,
    fill_slide_images,
    resolve_asset_bytes,
    resolve_slide_images,
)
from deckdna.pptx.composing.minimal import generate_deck
from deckdna.pptx.opc.package import OpcPackage
from PIL import Image

FIXTURES = Path(__file__).parent.parent / "fixtures"
PLAN_FIXTURE = FIXTURES / "content" / "poc_deck_plan.json"


def _png_bytes(color=(200, 30, 30), size=(24, 16)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_bytes(color=(30, 60, 200), size=(24, 16)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG")
    return buf.getvalue()


def _mk_asset(aid="img-0", artifact_id="pic.png", alt="Логотип", url=None):
    return Asset(
        id=aid,
        kind=Kind1.image,
        artifact_id=artifact_id,
        alt=alt,
        source_url=url,
    )


def _template_with_pic(path: Path) -> Path:
    """Шаблон: content-like слайд + реальный p:pic со стоковым растром."""
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    for i in range(4):
        slide.shapes.add_textbox(
            Inches(0.5), Inches(0.3 + i * 0.9), Inches(8), Inches(0.7)
        ).text_frame.text = (
            "Длинный абзац контентного текста про показатели "
            "и результаты проекта, который делает слайд content-like"
        )
    pic = tmp_pic = path.parent / "_stock.png"
    pic.write_bytes(_png_bytes(color=(10, 10, 10)))
    slide.shapes.add_picture(
        str(tmp_pic), Inches(1), Inches(4.2), Inches(3), Inches(2)
    )
    prs.save(str(path))
    tmp_pic.unlink()
    return path


def _template_text_only(path: Path) -> Path:
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    for i in range(4):
        slide.shapes.add_textbox(
            Inches(0.5), Inches(0.3 + i * 0.9), Inches(8), Inches(0.7)
        ).text_frame.text = (
            "Длинный абзац контентного текста про показатели "
            "и результаты проекта, который делает слайд content-like"
        )
    prs.save(str(path))
    return path


def _plan_with_image_unit(asset_ref="Логотип", extra_units=()) -> dict:
    plan = json.loads(PLAN_FIXTURE.read_text())
    plan["slides"] = plan["slides"][:1]
    units = [
        {"role": "title", "kind": "title", "text": "Картинка"},
        {"role": "body", "kind": "image", "asset_ref": asset_ref},
    ]
    units.extend(extra_units)
    plan["slides"][0]["content_units"] = units
    return plan


def _media_parts(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as zf:
        return {
            n: zf.read(n) for n in zf.namelist() if n.startswith("ppt/media/")
        }


# --- detect_image_content_type --------------------------------------


def test_detect_image_content_type():
    assert detect_image_content_type(_png_bytes()) == "image/png"
    assert detect_image_content_type(_jpeg_bytes()) == "image/jpeg"
    assert detect_image_content_type(b"GIF89a....") == "image/gif"
    assert detect_image_content_type(b"RIFF\x00\x00\x00\x00WEBPxx") == "image/webp"
    assert detect_image_content_type(b"\x00\x01random") is None


# --- resolve_asset_bytes --------------------------------------------


def test_resolve_bytes_markdown_local_path(tmp_path):
    """markdown `![alt](images/pic.png)` -> байты из папки content-файла."""
    content = tmp_path / "content.md"
    content.write_text("# T\n")
    img = tmp_path / "images"
    img.mkdir()
    (img / "pic.png").write_bytes(_png_bytes())
    data = resolve_asset_bytes(_mk_asset(artifact_id="images/pic.png"), content)
    assert data == (img / "pic.png").read_bytes()


def test_resolve_bytes_rejects_paths_outside_content_directory(tmp_path):
    content_dir = tmp_path / "upload"
    content_dir.mkdir()
    content = content_dir / "content.md"
    content.write_text("# T\n")
    outside = tmp_path / "private.png"
    outside.write_bytes(_png_bytes())

    for ref in ("../private.png", str(outside)):
        assert resolve_asset_bytes(_mk_asset(artifact_id=ref), content) is None


def test_resolve_bytes_rejects_symlink_escape(tmp_path):
    content_dir = tmp_path / "upload"
    content_dir.mkdir()
    content = content_dir / "content.md"
    content.write_text("# T\n")
    outside = tmp_path / "private.png"
    outside.write_bytes(_png_bytes())
    (content_dir / "linked.png").symlink_to(outside)

    assert resolve_asset_bytes(
        _mk_asset(artifact_id="linked.png"), content
    ) is None


def test_resolve_bytes_docx_inner_member(tmp_path):
    """`doc.docx#word/media/imageN.png` -> member того же docx-пакета."""
    import docx

    pic = tmp_path / "in.png"
    pic.write_bytes(_png_bytes(color=(1, 2, 3)))
    d = docx.Document()
    d.add_paragraph("x")
    d.add_picture(str(pic))
    docx_path = tmp_path / "report.docx"
    d.save(docx_path)
    asset = _mk_asset(
        artifact_id="report.docx#word/media/image1.png", alt=None
    )
    assert resolve_asset_bytes(asset, docx_path) == pic.read_bytes()


def test_resolve_bytes_missing_returns_none(tmp_path):
    """Исходный файл переехал/нет member -> честный None, не exception."""
    content = tmp_path / "content.md"
    content.write_text("# T\n")
    assert (
        resolve_asset_bytes(_mk_asset(artifact_id="gone/pic.png"), content)
        is None
    )
    assert (
        resolve_asset_bytes(
            _mk_asset(artifact_id="c.md#word/media/x.png"), content
        )
        is None
    )
    assert resolve_asset_bytes(_mk_asset(), None) is None


# --- resolve_slide_images (unit -> asset mapping) -------------------


def test_resolve_slide_images_alt_match(tmp_path):
    """markdown-проводка: unit.asset_ref = alt -> match по Asset.alt."""
    content = tmp_path / "c.md"
    content.write_text("# T\n")
    (tmp_path / "pic.png").write_bytes(_png_bytes())
    images, dropped = resolve_slide_images(
        ["Логотип"], [_mk_asset()], content, set()
    )
    assert dropped == 0 and len(images) == 1
    assert images[0].asset.id == "img-0"


def test_resolve_slide_images_none_ref_takes_next_unused(tmp_path):
    """asset_ref=None -> следующий неиспользованный asset по порядку;
    уже занятый (другим слайдом) не выдаётся повторно."""
    content = tmp_path / "c.md"
    content.write_text("# T\n")
    (tmp_path / "a.png").write_bytes(_png_bytes())
    (tmp_path / "b.png").write_bytes(_png_bytes(color=(9, 9, 9)))
    assets = [
        _mk_asset(aid="img-0", artifact_id="a.png"),
        _mk_asset(aid="img-1", artifact_id="b.png", alt=None),
    ]
    used = {"img-0"}
    images, dropped = resolve_slide_images([None], assets, content, used)
    assert dropped == 0 and images[0].asset.id == "img-1"


def test_resolve_slide_images_unmatched_drops(tmp_path):
    content = tmp_path / "c.md"
    content.write_text("# T\n")
    images, dropped = resolve_slide_images(["нет-такого"], [], content, set())
    assert images == [] and dropped == 1


def test_resolve_slide_images_generated_ref_bypasses_asset_pack(tmp_path):
    """ADR-017: a ref with no ContentPack match falls through to
    *generated* — already-in-memory bytes, no content_path re-read
    needed (content_path itself is None here, proving that path is
    never touched for a generated ref)."""
    png = _png_bytes()
    images, dropped = resolve_slide_images(
        ["generated:s0"], [], None, set(), generated={"generated:s0": png}
    )
    assert dropped == 0
    assert len(images) == 1
    assert images[0].data == png
    assert images[0].asset.id == "generated:s0"
    assert images[0].asset.kind == Kind1.image


def test_resolve_slide_images_real_asset_wins_over_generated_same_ref():
    """A real ContentPack asset always takes priority — generated is only
    consulted when nothing in the pack matches the ref."""
    images, dropped = resolve_slide_images(
        ["Логотип"],
        [_mk_asset()],
        None,
        set(),
        generated={"Логотип": b"should-never-be-used"},
    )
    # real asset path still needs content_path to read pic.png; without
    # it (None here) it drops -- proving generated did NOT shadow it.
    assert dropped == 1
    assert images == []


# --- fill_slide_images ----------------------------------------------


def test_fill_replaces_media_bytes(tmp_path):
    """Байты media-парта заменены на наши; content-type совпал (png->png)."""
    tpl = _template_with_pic(tmp_path / "tpl.pptx")
    pkg = OpcPackage.open(tpl)
    slide_part = "ppt/slides/slide1.xml"
    part = next(n for n in pkg.parts if n.startswith("ppt/media/"))
    assert pkg.parts[part] != _png_bytes()  # стоковый растр на месте
    rep = fill_slide_images(
        pkg.parts,
        slide_part,
        [ResolvedImage(_mk_asset(), _png_bytes(color=(5, 250, 5)))],
        pkg.rels(slide_part),
        set(),
    )
    assert rep.images_found == 1 and rep.images_filled == 1
    assert pkg.parts[part] == _png_bytes(color=(5, 250, 5))
    assert rep.content_type_overrides == 0


def test_fill_content_type_override_on_format_change(tmp_path):
    """jpeg-байты в .png-парт -> честный Override image/jpeg в
    [Content_Types].xml (не молчаливая подмена несовпадающего типа)."""
    tpl = _template_with_pic(tmp_path / "tpl.pptx")
    pkg = OpcPackage.open(tpl)
    slide_part = "ppt/slides/slide1.xml"
    part = next(n for n in pkg.parts if n.startswith("ppt/media/"))
    rep = fill_slide_images(
        pkg.parts,
        slide_part,
        [ResolvedImage(_mk_asset(), _jpeg_bytes())],
        pkg.rels(slide_part),
        set(),
    )
    assert rep.images_filled == 1 and rep.content_type_overrides == 1
    ct = pkg.parts["[Content_Types].xml"].decode()
    assert f'PartName="/{part}" ContentType="image/jpeg"' in ct


def test_fill_no_embedded_blip_drops(tmp_path):
    """Слайд без picture-плейсхолдера -> unit дропается, не падает."""
    tpl = _template_text_only(tmp_path / "tpl.pptx")
    pkg = OpcPackage.open(tpl)
    slide_part = "ppt/slides/slide1.xml"
    rep = fill_slide_images(
        pkg.parts,
        slide_part,
        [ResolvedImage(_mk_asset(), _png_bytes())],
        pkg.rels(slide_part),
        set(),
    )
    assert rep.images_found == 0 and rep.units_dropped == 1


# --- generate_deck wiring -------------------------------------------


def test_compose_image_fills_pic_part(tmp_path):
    """e2e: Kind.image unit + asset -> out.pptx media-парт несёт наши
    байты (не стоковый растр)."""
    tpl = _template_with_pic(tmp_path / "tpl.pptx")
    content = tmp_path / "c.md"
    content.write_text("# T\n")
    payload = _png_bytes(color=(0, 255, 0), size=(40, 30))
    (tmp_path / "pic.png").write_bytes(payload)
    report = generate_deck(
        tpl,
        _plan_with_image_unit(),
        tmp_path / "out.pptx",
        assets=[_mk_asset()],
        content_path=content,
    )
    img = report["slides"][0]["images"]
    assert img["images_filled"] == 1 and img["units_dropped"] == 0
    assert payload in _media_parts(tmp_path / "out.pptx").values()


def test_compose_image_no_placeholder_creates_pic(tmp_path):
    """Exemplar без picture-плейсхолдера -> create-fallback: новый p:pic
    + media-парт + rels-запись в опустевшем текстовом теле, не drop."""
    tpl = _template_text_only(tmp_path / "tpl.pptx")
    content = tmp_path / "c.md"
    content.write_text("# T\n")
    payload = _png_bytes()
    (tmp_path / "pic.png").write_bytes(payload)
    report = generate_deck(
        tpl,
        _plan_with_image_unit(),
        tmp_path / "out.pptx",
        assets=[_mk_asset()],
        content_path=content,
    )
    img = report["slides"][0]["images"]
    assert img["images_created"] == 1 and img["units_dropped"] == 0
    out = tmp_path / "out.pptx"
    assert payload in _media_parts(out).values()
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    pics = [
        s
        for sl in Presentation(out).slides
        for s in sl.shapes
        if s.shape_type == MSO_SHAPE_TYPE.PICTURE
    ]
    assert len(pics) == 1


def test_create_pic_drops_when_no_empty_host(tmp_path):
    """Сырой слайд без опустевших тел -> честный drop, не создание."""
    tpl = _template_text_only(tmp_path / "tpl.pptx")
    with zipfile.ZipFile(tpl) as zf:
        parts = {n: zf.read(n) for n in zf.namelist()}
    slide = "ppt/slides/slide1.xml"
    from deckdna.pptx.opc.package import parse_rels, rels_name_for

    rels = parse_rels(parts[rels_name_for(slide)])
    report = fill_slide_images(
        parts,
        slide,
        [ResolvedImage(asset=_mk_asset(), data=_png_bytes())],
        rels,
        set(),
        used_hosts=set(),
    )
    assert report.units_dropped == 1 and report.images_created == 0


def test_compose_image_unresolved_ref_drops(tmp_path):
    """asset_ref без asset в паке -> dropped."""
    tpl = _template_with_pic(tmp_path / "tpl.pptx")
    report = generate_deck(
        tpl,
        _plan_with_image_unit(asset_ref="нет-связи"),
        tmp_path / "out.pptx",
        assets=[],
        content_path=tmp_path / "c.md",
    )
    assert report["dropped_units"].get("image") == 1


def test_compose_image_missing_file_drops(tmp_path):
    """Asset есть, но файл на диске нет -> bytes_unresolved -> dropped."""
    tpl = _template_with_pic(tmp_path / "tpl.pptx")
    content = tmp_path / "c.md"
    content.write_text("# T\n")
    report = generate_deck(
        tpl,
        _plan_with_image_unit(),
        tmp_path / "out.pptx",
        assets=[_mk_asset(artifact_id="moved/pic.png")],
        content_path=content,
    )
    assert report["dropped_units"].get("image") == 1


def test_compose_second_unit_creates_pic(tmp_path):
    """Один media-парт не несёт две картинки: второй unit создаёт новый
    p:pic в опустевшем теле (create-fallback), не дропается."""
    tpl = _template_with_pic(tmp_path / "tpl.pptx")
    content = tmp_path / "c.md"
    content.write_text("# T\n")
    (tmp_path / "pic.png").write_bytes(_png_bytes())
    plan = _plan_with_image_unit(
        extra_units=[{"role": "body", "kind": "image", "asset_ref": "img-1"}]
    )
    report = generate_deck(
        tpl,
        plan,
        tmp_path / "out.pptx",
        assets=[_mk_asset(), _mk_asset(aid="img-1", alt="img-1")],
        content_path=content,
    )
    img = report["slides"][0]["images"]
    assert img["images_filled"] == 2 and img["images_created"] == 1
    assert img["units_dropped"] == 0


def test_generate_end_to_end_markdown_image(tmp_path):
    """Сквозной generate(): markdown `![alt](pic.png)` -> байты картинки
    реально в выходном pptx."""
    tpl = _template_with_pic(tmp_path / "tpl.pptx")
    payload = _png_bytes(color=(77, 77, 77), size=(48, 32))
    (tmp_path / "pic.png").write_bytes(payload)
    content = tmp_path / "c.md"
    content.write_text(
        "# Отчёт\n\nВводный абзац.\n\n![Логотип](pic.png)\n"
    )
    from deckdna.generation.pipeline import Brief, generate

    generate(
        tpl,
        content,
        Brief(
            purpose="Отчёт",
            audience="Жюри",
            language="ru",
            target_slide_count=10,
        ),
        tmp_path / "out",
    )
    deck = tmp_path / "out" / "deck.pptx"
    assert deck.exists()
    assert payload in _media_parts(deck).values()


def _content(tmp_path: Path) -> Path:
    p = tmp_path / "c.md"
    p.write_text("# Отчёт\n")
    return p


class _Resp:
    def __init__(self, data: bytes, headers=None):
        self._data = data
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, size=-1):
        return self._data if size < 0 else self._data[:size]


def _no_network(monkeypatch):
    """Remote fetch must not start for a forbidden destination."""

    def _boom(*a, **kw):
        raise AssertionError("remote fetch started for a forbidden address")

    monkeypatch.setattr(
        "deckdna.pptx.composing.image_fill._fetch_public_image", _boom
    )


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "127.0.0.2",
        "169.254.169.254",
        "192.168.1.10",
        "10.0.0.5",
        "172.16.3.4",
        "224.0.0.1",
        "::1",
        "0.0.0.0",  # noqa: S104
    ],
)
def test_ssrf_literal_private_host_dropped(tmp_path, monkeypatch, host):
    _no_network(monkeypatch)
    asset = _mk_asset(artifact_id="pic.png", url=f"http://{host}/a.png")
    assert resolve_asset_bytes(asset, _content(tmp_path)) is None


def _fake_dns(ip):
    def _getaddrinfo(host, port):
        return [(2, 1, 6, "", (ip, port or 80))]

    return _getaddrinfo


@pytest.mark.parametrize("ip", ["10.1.2.3", "192.168.0.7", "127.0.0.1", "169.254.169.254"])
def test_ssrf_dns_private_answer_dropped(tmp_path, monkeypatch, ip):
    _no_network(monkeypatch)
    monkeypatch.setattr(
        "deckdna.pptx.composing.image_fill.socket.getaddrinfo", _fake_dns(ip)
    )
    asset = _mk_asset(artifact_id="pic.png", url="https://evil.example/a.png")
    assert resolve_asset_bytes(asset, _content(tmp_path)) is None


def test_ssrf_mixed_dns_answer_dropped(tmp_path, monkeypatch):
    """Один публичный + один приватный ответ -> отказ (urlopen может выбрать любой)."""
    _no_network(monkeypatch)
    monkeypatch.setattr(
        "deckdna.pptx.composing.image_fill.socket.getaddrinfo",
        lambda host, port: [
            (2, 1, 6, "", ("93.184.216.34", 443)),
            (2, 1, 6, "", ("10.0.0.1", 443)),
        ],
    )
    asset = _mk_asset(artifact_id="pic.png", url="https://evil.example/a.png")
    assert resolve_asset_bytes(asset, _content(tmp_path)) is None


def test_public_url_fetch_ok(tmp_path, monkeypatch):
    payload = _png_bytes()
    monkeypatch.setattr(
        "deckdna.pptx.composing.image_fill.socket.getaddrinfo",
        _fake_dns("93.184.216.34"),
    )
    class Opener:
        def open(self, url, timeout):
            assert url == "https://cdn.example.com/a.png"
            assert timeout == 5
            return _Resp(payload)

    def make_opener(*handlers):
        assert any(isinstance(handler, _NoRedirect) for handler in handlers)
        return Opener()

    monkeypatch.setattr(urllib.request, "build_opener", make_opener)
    asset = _mk_asset(artifact_id="pic.png", url="https://cdn.example.com/a.png")
    assert resolve_asset_bytes(asset, _content(tmp_path)) == payload


def test_remote_image_redirect_is_disabled():
    handler = _NoRedirect()
    request = urllib.request.Request("https://cdn.example.com/a.png")
    assert handler.redirect_request(
        request, None, 302, "Found", {}, "http://169.254.169.254/latest/meta-data"
    ) is None


def test_remote_image_response_is_bounded(monkeypatch):
    class Opener:
        def __init__(self, response):
            self.response = response

        def open(self, url, timeout):
            return self.response

    over = b"x" * (_MAX_REMOTE_IMAGE_BYTES + 1)
    monkeypatch.setattr(
        urllib.request, "build_opener", lambda *handlers: Opener(_Resp(over))
    )
    assert _fetch_public_image("https://cdn.example.com/a.png") is None

    monkeypatch.setattr(
        urllib.request,
        "build_opener",
        lambda *handlers: Opener(_Resp(b"", {"Content-Length": str(len(over))})),
    )
    assert _fetch_public_image("https://cdn.example.com/a.png") is None


def test_dns_failure_dropped(tmp_path, monkeypatch):
    _no_network(monkeypatch)

    def _fail(host, port):
        raise OSError("name resolution failed")

    monkeypatch.setattr(
        "deckdna.pptx.composing.image_fill.socket.getaddrinfo", _fail
    )
    asset = _mk_asset(artifact_id="pic.png", url="https://gone.example/a.png")
    assert resolve_asset_bytes(asset, _content(tmp_path)) is None


def _template_pic_off_pool(path: Path) -> Path:
    """Шаблон: 2 content-like слайда в dominant layout + p:pic-слайд
    вне ranked-пула (как slide21-23 для chart в lct2026_submission)."""
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    for _ in range(2):
        s = prs.slides.add_slide(prs.slide_layouts[6])
        for i in range(4):
            s.shapes.add_textbox(
                Inches(0.5), Inches(0.3 + i * 0.9), Inches(8), Inches(0.7)
            ).text_frame.text = (
                "Длинный абзац контентного текста про показатели "
                "и результаты проекта, который делает слайд content-like"
            )
    s = prs.slides.add_slide(prs.slide_layouts[5])
    tmp_pic = path.parent / "_stock_off.png"
    tmp_pic.write_bytes(_png_bytes(color=(10, 10, 10)))
    s.shapes.add_picture(str(tmp_pic), Inches(1), Inches(1), Inches(3), Inches(2))
    prs.save(str(path))
    tmp_pic.unlink()
    return path


def test_exemplar_needs_prefer_pic_slide(tmp_path):
    """need=image -> exemplar с реальным a:blip/r:embed вне dominant
    пула (image-rel, на который никто не ссылается blip'ом, не считается)."""
    from deckdna.pptx.cloning.exemplar import select_exemplar_slides

    tpl = _template_pic_off_pool(tmp_path / "tpl.pptx")
    pkg = OpcPackage.open(tpl)
    picks = select_exemplar_slides(pkg, 1, needs=[{"image"}])
    assert picks[0].slide_part == "ppt/slides/slide3.xml"


def test_generate_deck_image_filled_via_capability_match(tmp_path):
    """e2e: image unit получает pic-несущий exemplar и байты ассета
    реально в выходном media-парте (до фикса — честный drop: у пиков
    dominant пула нет ни одного blip-embed)."""
    tpl = _template_pic_off_pool(tmp_path / "tpl.pptx")
    content = tmp_path / "c.md"
    content.write_text("# T\n")
    payload = _png_bytes(color=(0, 255, 0), size=(40, 30))
    (tmp_path / "pic.png").write_bytes(payload)
    report = generate_deck(
        tpl,
        _plan_with_image_unit(),
        tmp_path / "out.pptx",
        assets=[_mk_asset()],
        content_path=content,
    )
    slide_rep = report["slides"][0]
    assert slide_rep["exemplar"]["slide_part"] == "ppt/slides/slide3.xml"
    assert slide_rep["images"]["images_filled"] == 1
    assert report["dropped_units"].get("image", 0) == 0
    assert payload in _media_parts(tmp_path / "out.pptx").values()


def test_generate_deck_fills_generated_image_no_content_pack_asset(tmp_path):
    """ADR-017 e2e: a Kind.image unit whose asset_ref matches NO
    ContentPack asset (the model-generated case) still lands real bytes
    in the output .pptx via generate_deck's generated_images param — no
    content_path, no assets list needed at all for this ref."""
    tpl = _template_pic_off_pool(tmp_path / "tpl.pptx")
    payload = _png_bytes(color=(1, 2, 3), size=(50, 40))
    report = generate_deck(
        tpl,
        _plan_with_image_unit(asset_ref="generated:s0"),
        tmp_path / "out.pptx",
        generated_images={"generated:s0": payload},
    )
    slide_rep = report["slides"][0]
    assert slide_rep["exemplar"]["slide_part"] == "ppt/slides/slide3.xml"
    assert slide_rep["images"]["images_filled"] == 1
    assert report["dropped_units"].get("image", 0) == 0
    assert payload in _media_parts(tmp_path / "out.pptx").values()


def test_generate_end_to_end_docx_image(tmp_path):
    """Сквозной generate(): docx со встроенной картинкой -> байты из
    `doc.docx#word/media/imageN.png` реально в выходном pptx
    (docx-путь image_ref добавлен после markdown)."""
    import docx

    tpl = _template_with_pic(tmp_path / "tpl.pptx")
    payload = _png_bytes(color=(33, 120, 90), size=(40, 30))
    d = docx.Document()
    d.add_heading("Отчёт", level=1)
    d.add_paragraph("Вводный абзац контентного текста про результаты.")
    d.add_picture(BytesIO(payload))
    d.add_paragraph("Заключение.")
    content = tmp_path / "report.docx"
    d.save(content)

    from deckdna.generation.pipeline import Brief, generate

    generate(
        tpl,
        content,
        Brief(
            purpose="Отчёт",
            audience="Жюри",
            language="ru",
            target_slide_count=10,
        ),
        tmp_path / "out",
    )
    deck = tmp_path / "out" / "deck.pptx"
    assert deck.exists()
    assert payload in _media_parts(deck).values()


def test_generate_creates_pic_on_pictureless_template(tmp_path):
    """Сквозной generate(): шаблон вообще без p:pic + markdown-картинка
    -> новый p:pic с байтами в новом media-парте (не drop)."""
    tpl = _template_text_only(tmp_path / "tpl.pptx")
    payload = _png_bytes(color=(50, 150, 60), size=(30, 20))
    (tmp_path / "pic.png").write_bytes(payload)
    content = tmp_path / "c.md"
    content.write_text("# Отчёт\n\nВводный абзац.\n\n![Логотип](pic.png)\n")
    from deckdna.generation.pipeline import Brief, generate

    generate(
        tpl,
        content,
        Brief(
            purpose="Отчёт",
            audience="Жюри",
            language="ru",
            target_slide_count=10,
        ),
        tmp_path / "out",
    )
    deck = tmp_path / "out" / "deck.pptx"
    assert deck.exists()
    assert payload in _media_parts(deck).values()
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    pics = [
        s
        for sl in Presentation(deck).slides
        for s in sl.shapes
        if s.shape_type == MSO_SHAPE_TYPE.PICTURE
    ]
    assert pics


# --- shared media-part unshare --------------------------------------


def test_unshare_shared_media_part(tmp_path):
    """Два выходных слайда делят один media-парт (один pic-exemplar):
    unshare репоинтит image-rel второго слайда на дубликат с PRISTINE
    байтами — не с уже перезаписанными первым слайдом; тип дубликата
    берётся из ИСХОДНОГО [Content_Types].xml."""
    from deckdna.pptx.composing.image_fill import unshare_image_parts
    from deckdna.pptx.opc.package import parse_rels, rels_name_for

    tpl = _template_with_pic(tmp_path / "tpl.pptx")
    with zipfile.ZipFile(tpl) as zf:
        src_parts = {n: zf.read(n) for n in zf.namelist()}
    media = next(n for n in src_parts if n.startswith("ppt/media/"))
    # Второй выходной слайд — клон первого: тот же XML и тот же image-rel.
    out_parts = dict(src_parts)
    out_parts["ppt/slides/slide2.xml"] = src_parts["ppt/slides/slide1.xml"]
    out_parts[rels_name_for("ppt/slides/slide2.xml")] = src_parts[
        rels_name_for("ppt/slides/slide1.xml")
    ]
    used: set[str] = set()
    payload1 = _png_bytes(color=(1, 2, 3))
    payload2 = _png_bytes(color=(200, 100, 50))
    a1 = _mk_asset(aid="a1")
    a2 = _mk_asset(aid="a2")

    # Первый слайд заполняется первым — забирает оригинальный парт.
    r1 = fill_slide_images(
        out_parts,
        "ppt/slides/slide1.xml",
        [ResolvedImage(asset=a1, data=payload1)],
        parse_rels(out_parts[rels_name_for("ppt/slides/slide1.xml")]),
        used,
    )
    assert r1.images_filled == 1 and media in used
    assert out_parts[media] == payload1  # оригинал уже мутирован

    unshare_image_parts(
        out_parts, src_parts, "ppt/slides/slide2.xml", used, "dup2"
    )
    rels2 = parse_rels(out_parts[rels_name_for("ppt/slides/slide2.xml")])
    img_rel = next(r for r in rels2 if r.type.endswith("/image"))
    dup = f"ppt/media/{img_rel.target.split('/')[-1]}"
    assert dup != media and "_dup" in dup
    assert out_parts[dup] == src_parts[media]  # pristine, не payload1

    r2 = fill_slide_images(
        out_parts,
        "ppt/slides/slide2.xml",
        [ResolvedImage(asset=a2, data=payload2)],
        rels2,
        used,
    )
    assert r2.images_filled == 1 and r2.units_dropped == 0
    assert out_parts[media] == payload1
    assert out_parts[dup] == payload2


def test_unshare_missing_src_keeps_shared(tmp_path):
    """Нет pristine-байт в src_parts -> rel не трогаем (drop downstream)."""
    from deckdna.pptx.composing.image_fill import unshare_image_parts
    from deckdna.pptx.opc.package import parse_rels, rels_name_for

    tpl = _template_with_pic(tmp_path / "tpl.pptx")
    with zipfile.ZipFile(tpl) as zf:
        parts = {n: zf.read(n) for n in zf.namelist()}
    media = next(n for n in parts if n.startswith("ppt/media/"))
    used = {media}
    src = dict(parts)
    del src[media]
    unshare_image_parts(parts, src, "ppt/slides/slide1.xml", used, "dup")
    rels = parse_rels(parts[rels_name_for("ppt/slides/slide1.xml")])
    img_rel = next(r for r in rels if r.type.endswith("/image"))
    assert "_dup" not in img_rel.target


def test_generate_two_image_slides_share_exemplar(tmp_path):
    """E2E: 2 image-юнита, в шаблоне ОДИН pic-несущий слайд — оба выходных
    слайда несут свои байты через unshare-дубликат (не drop второго)."""
    tpl = _template_with_pic(tmp_path / "tpl.pptx")
    p1 = _png_bytes(color=(10, 20, 30), size=(16, 12))
    p2 = _png_bytes(color=(200, 100, 50), size=(16, 12))
    (tmp_path / "a.png").write_bytes(p1)
    (tmp_path / "b.png").write_bytes(p2)
    content = tmp_path / "c.md"
    content.write_text("# T\n")
    plan = json.loads(PLAN_FIXTURE.read_text())
    plan["slides"] = plan["slides"][:2]
    for i, slide in enumerate(plan["slides"]):
        slide["content_units"] = [
            {"role": "title", "kind": "title", "text": f"Слайд {i}"},
            {"role": "body", "kind": "image", "asset_ref": f"img-{i}"},
        ]
    report = generate_deck(
        tpl,
        plan,
        tmp_path / "out.pptx",
        assets=[
            _mk_asset(aid="a1", artifact_id="a.png", alt="img-0"),
            _mk_asset(aid="b1", artifact_id="b.png", alt="img-1"),
        ],
        content_path=content,
    )
    media = _media_parts(tmp_path / "out.pptx")
    assert p1 in media.values() and p2 in media.values()
    assert len([n for n in media if "_dup" in n]) == 1
    for s in report["slides"][:2]:
        assert s["images"]["images_filled"] == 1
        assert s["images"]["units_dropped"] == 0
    from pptx import Presentation

    pics = [
        s
        for sl in Presentation(tmp_path / "out.pptx").slides
        for s in sl.shapes
        if s.shape_type == 13  # MSO_SHAPE_TYPE.PICTURE
    ]
    assert len(pics) >= 2
