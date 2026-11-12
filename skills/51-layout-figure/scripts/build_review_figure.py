# -*- coding: utf-8 -*-
"""조판 빌드 예시 — 검토구간 위치도(51 SKILL 1~3절 문법을 코드로).

run_headless.py 가 exec 한다(전역 proj · BUILD_ARGS · HEADLESS). QGIS MCP(라이브 QGIS)의 execute_code 로
돌려도 된다(그때는 내보내기를 하지 말 것 — 150 dpi 이상이면 QGIS 가 죽는다).

하는 일 (그 조판 한 장에만 — 프로젝트의 레이어 스타일은 건드리지 않는다)
  1. 관로 레이어를 복제해 룰 기반 심볼: 검토구간 = 검정 굵은 선 + 흰 테두리 + 뒤에 반투명 색띠,
     압송 = 검정 점선, 그 밖 = 회색 가는 선. **심볼 레벨 켬**(안 켜면 접합부에서 흰 테두리가 검정선을 끊는다).
  2. 그 스타일을 조판 지도에만 오버라이드: setKeepLayerSet + **setKeepLayerStyles(True)** + setLayerStyleOverrides.
  3. 구간 번호 라벨(알약 바탕, PAL 우선순위 10)을 같은 오버라이드에 넣는다.
  4. 범례: 자동갱신 끄고 트리를 **제자리에서** 비운 뒤 다시 채움, 지도 필터 켬, 오버라이드 반영, 제목 숨김.
  5. 제목 라벨 아이템(id='제목').
  범례 위치는 run_headless.py 가 크기 확정 후 모서리에 붙인다.

BUILD_ARGS (JSON) 예:
{
  "layout": "검토구간 위치도",
  "pipe_layer": "계획관로",               # QGIS 프로젝트 안 레이어 이름
  "id_field": "관번호",
  "kind_field": "구분", "pressure_value": "압송",      # 선택
  "background": ["위성지도"],              # 선택 — 지도에 같이 보일 레이어(아래에 깔림)
  "sections": [{"pipes": ["XX-A-001", "XX-A-002"], "color": "#FFD400", "label": "① XX-A-001~002"},
               {"pipes": ["XX-B-010"], "color": "#00C2E0", "label": "② XX-B-010"}],
  "title": "기존시설 검토구간 위치도",
  "zoom_to_sections": true, "zoom_margin": 0.15
}
"""
from qgis.core import (QgsLayoutItemLabel, QgsLayoutItemLegend, QgsLayoutItemMap,
                       QgsLayoutPoint, QgsLayoutSize, QgsLegendRenderer, QgsLegendStyle, QgsLineSymbol,
                       QgsMapLayerStyle, QgsPalLayerSettings, QgsProject, QgsRectangle, QgsRuleBasedLabeling,
                       QgsRuleBasedRenderer, QgsSimpleLineSymbolLayer, QgsTextBackgroundSettings,
                       QgsTextBufferSettings, QgsTextFormat, QgsUnitTypes)
from qgis.PyQt.QtCore import QSizeF
from qgis.PyQt.QtGui import QColor, QFont

A = globals().get('BUILD_ARGS') or {}
proj = QgsProject.instance()
FONT = A.get('font', '맑은 고딕')
BAND_W, BAND_ALPHA = float(A.get('band_mm', 9.0)), int(A.get('band_alpha', 130))   # 띠 폭 mm, 투명도 0~255

l = proj.layoutManager().layoutByName(A['layout'])
if l is None:
    raise SystemExit('조판이 없다: %s' % A['layout'])
maps = [it for it in l.items() if isinstance(it, QgsLayoutItemMap)]
if not maps:
    raise SystemExit('조판에 지도 아이템이 없다')
mp = maps[0]


def layer(name):
    hit = proj.mapLayersByName(name)
    if not hit:
        raise SystemExit('레이어가 없다: %s' % name)
    return hit[0]


def line_sym(col, w, dash=False, casing=1.8, band=None):
    """[띠(pass 0)] + 흰 테두리(pass 1) + 본선(pass 2)."""
    sym = QgsLineSymbol.createSimple({'color': '#FFFFFF', 'width': str(w + casing),
                                      'capstyle': 'round', 'joinstyle': 'round'})
    sym.symbolLayer(0).setRenderingPass(1)
    if band:
        c = QColor(band)
        c.setAlpha(BAND_ALPHA)
        bl = QgsSimpleLineSymbolLayer.create({'color': '%d,%d,%d,%d' % (c.red(), c.green(), c.blue(), c.alpha()),
                                              'width': str(BAND_W), 'capstyle': 'round', 'joinstyle': 'round'})
        bl.setRenderingPass(0)
        sym.insertSymbolLayer(0, bl)
    p = {'color': col, 'width': str(w), 'capstyle': 'round', 'joinstyle': 'round'}
    if dash:
        p.update({'use_custom_dash': '1', 'customdash': '5;2.5', 'customdash_unit': 'MM', 'capstyle': 'flat'})
    ml = QgsSimpleLineSymbolLayer.create(p)
    ml.setRenderingPass(2)
    sym.appendSymbolLayer(ml)
    return sym


def pill(col, fg):
    f = QgsTextFormat()
    q = QFont(FONT)
    q.setBold(True)
    f.setFont(q)
    f.setSize(9)
    f.setColor(QColor(fg))
    g = QgsTextBackgroundSettings()
    g.setEnabled(True)
    g.setType(QgsTextBackgroundSettings.ShapeRectangle)
    g.setSizeType(QgsTextBackgroundSettings.SizeBuffer)
    g.setSize(QSizeF(1.6, 0.9))
    g.setRadii(QSizeF(1.8, 1.8))
    g.setFillColor(QColor(col))
    g.setStrokeColor(QColor('#FFFFFF'))
    g.setStrokeWidth(0.5)
    f.setBackground(g)
    return f


def quoted(v):
    return "'%s'" % str(v).replace("'", "''")


pipe = layer(A['pipe_layer'])
fid = '"%s"' % A.get('id_field', '관번호')
secs = A.get('sections', [])

# 1·3) 관로 심볼 + 구간 라벨 → 복제본에 만들어 스타일 XML 로
c = pipe.clone()
root = QgsRuleBasedRenderer.Rule(None)
lroot = QgsRuleBasedLabeling.Rule(None)
for k, s in enumerate(secs, 1):
    expr = '%s IN (%s)' % (fid, ','.join(quoted(x) for x in s['pipes']))
    lab = s.get('label') or '%d' % k
    root.appendChild(QgsRuleBasedRenderer.Rule(line_sym('#111111', 2.4, band=s.get('color', '#FFD400')),
                                               0, 0, expr, lab))
    st = QgsPalLayerSettings()
    st.fieldName = "'%s'" % lab.replace("'", "''")
    st.isExpression = True
    st.placement = QgsPalLayerSettings.Horizontal
    st.priority = 10                                  # 구간·펌프 10 > 압송 8 > 보조 3
    st.setFormat(pill(s.get('color', '#FFD400'), s.get('fg', '#111111')))
    first = QgsRuleBasedLabeling.Rule(st)
    first.setFilterExpression('%s = %s' % (fid, quoted(s['pipes'][0])))   # 구간 첫 관에만 하나
    lroot.appendChild(first)
if A.get('kind_field') and A.get('pressure_value'):
    root.appendChild(QgsRuleBasedRenderer.Rule(line_sym('#111111', 2.0, dash=True), 0, 0,
                                               '"%s" = %s' % (A['kind_field'], quoted(A['pressure_value'])), '압송관'))
els = QgsRuleBasedRenderer.Rule(line_sym('#555555', 1.0, casing=1.2), 0, 0, 'ELSE', '기타 관로')
els.setIsElse(True)
root.appendChild(els)
rr = QgsRuleBasedRenderer(root)
rr.setUsingSymbolLevels(True)                         # ★ 접합부 끊김 방지
c.setRenderer(rr)
c.setLabeling(QgsRuleBasedLabeling(lroot))
c.setLabelsEnabled(True)
st_xml = QgsMapLayerStyle()
st_xml.readFromLayer(c)

# 2) 이 조판 지도에만 적용
lyrs = [pipe] + [layer(n) for n in A.get('background', [])]
mp.setKeepLayerSet(True)
mp.setLayers(lyrs)
mp.setKeepLayerStyles(True)                           # ★ 안 켜면 다시 열 때 오버라이드가 사라진다
mp.setLayerStyleOverrides({pipe.id(): st_xml.xmlData()})
if A.get('zoom_to_sections', True) and secs:
    ids = {x for s in secs for x in s['pipes']}
    idx = pipe.fields().indexOf(A.get('id_field', '관번호'))
    ext = QgsRectangle()
    ext.setMinimal()
    for f in pipe.getFeatures():
        if str(f.attributes()[idx]) in ids:
            ext.combineExtentWith(f.geometry().boundingBox())
    if not ext.isEmpty():
        m = float(A.get('zoom_margin', 0.15))
        ext.grow(max(ext.width(), ext.height()) * m)
        mp.zoomToExtent(ext)
mp.invalidateCache()

# 4) 범례 — 조판에 넣은 뒤 자동갱신 끄고 트리를 제자리에서 다시 채운다
lgs = [it for it in l.items() if isinstance(it, QgsLayoutItemLegend)]
if lgs:
    lg = lgs[0]
else:
    lg = QgsLayoutItemLegend(l)
    l.addLayoutItem(lg)                               # 먼저 넣어야 저장되는 트리가 생긴다
    lg.setId('범례')
    lg.setTitle(A.get('legend_title', '범  례'))
lg.setLinkedMap(mp)
lg.setAutoUpdateModel(False)
lg.setLegendFilterByMapEnabled(True)                  # ★ 안 켜면 다시 열 때 원본 스타일로 그려진다
rg = lg.model().rootGroup()                           # ★ setRootGroup(새 트리) 금지
for ch in list(rg.children()):
    rg.removeChildNode(ch)
rg.addLayer(pipe)
lg.model().setLayerStyleOverrides(mp.layerStyleOverrides())
for node in rg.findLayers():
    lg.model().refreshLayerLegend(node)
    QgsLegendRenderer.setNodeLegendStyle(node, QgsLegendStyle.Hidden)
lg.setStyleFont(QgsLegendStyle.Title, QFont(FONT, 10, QFont.Bold))
lg.setStyleFont(QgsLegendStyle.SymbolLabel, QFont(FONT, 8))
lg.setBackgroundEnabled(True)
lg.setBackgroundColor(QColor(255, 255, 255, 235))
lg.setFrameEnabled(True)
lg.setResizeToContents(True)
lg.refresh()

# 5) 제목
if A.get('title'):
    tl = next((it for it in l.items() if isinstance(it, QgsLayoutItemLabel) and it.id() == '제목'), None)
    if tl is None:
        tl = QgsLayoutItemLabel(l)
        tl.setId('제목')
        l.addLayoutItem(tl)
    tl.setText(A['title'])
    f = QFont(FONT)
    f.setPointSizeF(float(A.get('title_pt', 16)))
    f.setBold(True)
    tl.setFont(f)
    tl.setFontColor(QColor(A.get('title_color', '#FFFFFF')))
    tl.setBackgroundEnabled(True)
    tl.setBackgroundColor(QColor(A.get('title_bg', '#2A5346')))
    tl.setMargin(2.0)
    tl.attemptMove(QgsLayoutPoint(8, 6, QgsUnitTypes.LayoutMillimeters))
    tl.attemptResize(QgsLayoutSize(float(A.get('title_w_mm', 120)), float(A.get('title_h_mm', 14)),
                                   QgsUnitTypes.LayoutMillimeters))
l.refresh()
print('빌드: 구간 %d · 지도 레이어 %d · 범례 노드 %d'
      % (len(secs), len(lyrs), sum(len(lg.model().layerLegendNodes(n)) for n in rg.findLayers())))
