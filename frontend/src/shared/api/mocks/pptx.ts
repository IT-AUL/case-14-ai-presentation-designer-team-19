import { createZip } from './zip'
import type { ZipEntry } from './zip'

export const PPTX_MIME = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'

export interface PlaceholderSlide {
  title: string
  lines: readonly string[]
}

const XML_HEADER = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
const NS_A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
const NS_R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
const NS_P = 'http://schemas.openxmlformats.org/presentationml/2006/main'
const NS_REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
const REL_TYPE = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
const CT = 'application/vnd.openxmlformats-officedocument'
const SLIDE_WIDTH = 12192000
const SLIDE_HEIGHT = 6858000

function escapeXml(value: string): string {
  return value.replace(/[<>&"']/g, (char) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&apos;' })[char] ?? char)
}

function relationships(items: readonly { id: string; type: string; target: string }[]): string {
  const body = items.map((item) => `<Relationship Id="${item.id}" Type="${REL_TYPE}/${item.type}" Target="${item.target}"/>`).join('')
  return `${XML_HEADER}<Relationships xmlns="${NS_REL}">${body}</Relationships>`
}

const GROUP_HEADER =
  '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>' +
  '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'

function textBox(id: number, name: string, box: { x: number; y: number; cx: number; cy: number }, paragraphs: readonly string[], size: number, bold: boolean): string {
  const runs = paragraphs
    .map((text) => `<a:p><a:r><a:rPr lang="ru-RU" sz="${size}"${bold ? ' b="1"' : ''} dirty="0"/><a:t>${escapeXml(text)}</a:t></a:r></a:p>`)
    .join('')
  return (
    `<p:sp><p:nvSpPr><p:cNvPr id="${id}" name="${name}"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>` +
    `<p:spPr><a:xfrm><a:off x="${box.x}" y="${box.y}"/><a:ext cx="${box.cx}" cy="${box.cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/></p:spPr>` +
    `<p:txBody><a:bodyPr wrap="square" rtlCol="0"><a:normAutofit/></a:bodyPr><a:lstStyle/>${runs || '<a:p><a:endParaRPr lang="ru-RU"/></a:p>'}</p:txBody></p:sp>`
  )
}

function slideXml(slide: PlaceholderSlide): string {
  const title = textBox(2, 'Title', { x: 762000, y: 685800, cx: 10668000, cy: 1371600 }, [slide.title], 3200, true)
  const body = textBox(3, 'Body', { x: 762000, y: 2286000, cx: 10668000, cy: 3886200 }, slide.lines, 1800, false)
  return (
    `${XML_HEADER}<p:sld xmlns:a="${NS_A}" xmlns:r="${NS_R}" xmlns:p="${NS_P}">` +
    `<p:cSld><p:spTree>${GROUP_HEADER}${title}${body}</p:spTree></p:cSld>` +
    '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>'
  )
}

const THEME_XML =
  `${XML_HEADER}<a:theme xmlns:a="${NS_A}" name="DeckDNA Placeholder"><a:themeElements>` +
  '<a:clrScheme name="DeckDNA Placeholder">' +
  '<a:dk1><a:srgbClr val="000000"/></a:dk1><a:lt1><a:srgbClr val="FFFFFF"/></a:lt1>' +
  '<a:dk2><a:srgbClr val="1F2937"/></a:dk2><a:lt2><a:srgbClr val="F3F4F6"/></a:lt2>' +
  '<a:accent1><a:srgbClr val="2563EB"/></a:accent1><a:accent2><a:srgbClr val="0EA5E9"/></a:accent2>' +
  '<a:accent3><a:srgbClr val="10B981"/></a:accent3><a:accent4><a:srgbClr val="F59E0B"/></a:accent4>' +
  '<a:accent5><a:srgbClr val="EF4444"/></a:accent5><a:accent6><a:srgbClr val="8B5CF6"/></a:accent6>' +
  '<a:hlink><a:srgbClr val="2563EB"/></a:hlink><a:folHlink><a:srgbClr val="7C3AED"/></a:folHlink>' +
  '</a:clrScheme>' +
  '<a:fontScheme name="DeckDNA Placeholder">' +
  '<a:majorFont><a:latin typeface="Arial"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont>' +
  '<a:minorFont><a:latin typeface="Arial"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont>' +
  '</a:fontScheme>' +
  '<a:fmtScheme name="DeckDNA Placeholder">' +
  `<a:fillStyleLst>${'<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'.repeat(3)}</a:fillStyleLst>` +
  `<a:lnStyleLst>${'<a:ln w="6350"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'.repeat(3)}</a:lnStyleLst>` +
  `<a:effectStyleLst>${'<a:effectStyle><a:effectLst/></a:effectStyle>'.repeat(3)}</a:effectStyleLst>` +
  `<a:bgFillStyleLst>${'<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'.repeat(3)}</a:bgFillStyleLst>` +
  '</a:fmtScheme></a:themeElements><a:objectDefaults/><a:extraClrSchemeLst/></a:theme>'

const MASTER_XML =
  `${XML_HEADER}<p:sldMaster xmlns:a="${NS_A}" xmlns:r="${NS_R}" xmlns:p="${NS_P}">` +
  `<p:cSld><p:spTree>${GROUP_HEADER}</p:spTree></p:cSld>` +
  '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>' +
  '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst></p:sldMaster>'

const LAYOUT_XML =
  `${XML_HEADER}<p:sldLayout xmlns:a="${NS_A}" xmlns:r="${NS_R}" xmlns:p="${NS_P}" type="blank" preserve="1">` +
  `<p:cSld name="Blank"><p:spTree>${GROUP_HEADER}</p:spTree></p:cSld>` +
  '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>'

export function createPlaceholderPptx(slides: readonly PlaceholderSlide[]): Uint8Array<ArrayBuffer> {
  const count = Math.max(slides.length, 1)
  const deck = slides.length > 0 ? slides : [{ title: 'DeckDNA', lines: [] }]
  const slideIds = Array.from({ length: count }, (_, index) => index + 1)

  const contentTypes =
    `${XML_HEADER}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">` +
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>' +
    '<Default Extension="xml" ContentType="application/xml"/>' +
    `<Override PartName="/ppt/presentation.xml" ContentType="${CT}.presentationml.presentation.main+xml"/>` +
    `<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="${CT}.presentationml.slideMaster+xml"/>` +
    `<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="${CT}.presentationml.slideLayout+xml"/>` +
    `<Override PartName="/ppt/theme/theme1.xml" ContentType="${CT}.theme+xml"/>` +
    slideIds.map((id) => `<Override PartName="/ppt/slides/slide${id}.xml" ContentType="${CT}.presentationml.slide+xml"/>`).join('') +
    '</Types>'

  const presentation =
    `${XML_HEADER}<p:presentation xmlns:a="${NS_A}" xmlns:r="${NS_R}" xmlns:p="${NS_P}">` +
    '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>' +
    `<p:sldIdLst>${slideIds.map((id) => `<p:sldId id="${255 + id}" r:id="rId${id + 2}"/>`).join('')}</p:sldIdLst>` +
    `<p:sldSz cx="${SLIDE_WIDTH}" cy="${SLIDE_HEIGHT}"/><p:notesSz cx="6858000" cy="9144000"/>` +
    '</p:presentation>'

  const entries: ZipEntry[] = [
    { path: '[Content_Types].xml', content: contentTypes },
    { path: '_rels/.rels', content: relationships([{ id: 'rId1', type: 'officeDocument', target: 'ppt/presentation.xml' }]) },
    { path: 'ppt/presentation.xml', content: presentation },
    {
      path: 'ppt/_rels/presentation.xml.rels',
      content: relationships([
        { id: 'rId1', type: 'slideMaster', target: 'slideMasters/slideMaster1.xml' },
        { id: 'rId2', type: 'theme', target: 'theme/theme1.xml' },
        ...slideIds.map((id) => ({ id: `rId${id + 2}`, type: 'slide', target: `slides/slide${id}.xml` })),
      ]),
    },
    { path: 'ppt/slideMasters/slideMaster1.xml', content: MASTER_XML },
    {
      path: 'ppt/slideMasters/_rels/slideMaster1.xml.rels',
      content: relationships([
        { id: 'rId1', type: 'slideLayout', target: '../slideLayouts/slideLayout1.xml' },
        { id: 'rId2', type: 'theme', target: '../theme/theme1.xml' },
      ]),
    },
    { path: 'ppt/slideLayouts/slideLayout1.xml', content: LAYOUT_XML },
    {
      path: 'ppt/slideLayouts/_rels/slideLayout1.xml.rels',
      content: relationships([{ id: 'rId1', type: 'slideMaster', target: '../slideMasters/slideMaster1.xml' }]),
    },
    { path: 'ppt/theme/theme1.xml', content: THEME_XML },
    ...deck.flatMap((slide, index) => [
      { path: `ppt/slides/slide${index + 1}.xml`, content: slideXml(slide) },
      {
        path: `ppt/slides/_rels/slide${index + 1}.xml.rels`,
        content: relationships([{ id: 'rId1', type: 'slideLayout', target: '../slideLayouts/slideLayout1.xml' }]),
      },
    ]),
  ]
  return createZip(entries)
}
