# -*- coding: utf-8 -*-
"""GUI 없이 프로젝트의 조판을 고해상 PNG/PDF 로 내보내기만 한다(프로젝트는 저장하지 않는다).

QGIS MCP(라이브 QGIS)에서 150 dpi 이상으로 내보내면 QGIS 가 죽는다 → 조판을 GUI 에서 손으로 다 만든 뒤
이 스크립트로 내보낸다. 빌드까지 같이 하려면 run_headless.py.

사용 (python-qgis-ltr.bat 로):
  "<QGIS>\\bin\\python-qgis-ltr.bat" export_headless.py <프로젝트.qgz> --list
  "<QGIS>\\bin\\python-qgis-ltr.bat" export_headless.py <프로젝트.qgz> --layout "조판 이름" [--layout …] -o <출력 폴더>
        [--dpi 300] [--formats png,pdf] [--legend-layers "레이어A;레이어B"] [--legend-corner br]
  --all 이면 모든 조판.

--legend-layers: 범례를 이 레이어들로 다시 채운다(51 SKILL 3절 규칙대로 — 자동갱신 끄고 트리를 제자리에서
비운 뒤 addLayer, 지도 필터 켜고 지도 스타일 오버라이드 반영, 레이어 제목 숨김). 안 주면 범례는 그대로.
★ QT_QPA_PLATFORM=offscreen 을 쓰지 말 것 — 한글 글꼴이 깨진다.
"""
import argparse
import os
import re
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run_headless import prefix_path, place_legends, export  # noqa: E402


def rebuild_legend(proj, layout, names):
    from qgis.core import QgsLayoutItemLegend, QgsLayoutItemMap, QgsLegendRenderer, QgsLegendStyle
    maps = [it for it in layout.items() if isinstance(it, QgsLayoutItemMap)]
    lyrs = []
    for nm in names:
        hit = proj.mapLayersByName(nm)
        if not hit:
            print('  범례: 레이어 없음 —', nm)
            continue
        lyrs.append(hit[0])
    for lg in [it for it in layout.items() if isinstance(it, QgsLayoutItemLegend)]:
        mp = lg.linkedMap() or (maps[0] if maps else None)
        if mp is not None:
            lg.setLinkedMap(mp)
        lg.setAutoUpdateModel(False)
        lg.setLegendFilterByMapEnabled(True)          # 안 켜면 다시 열 때 원본 스타일로 그려진다
        rg = lg.model().rootGroup()                   # ★ setRootGroup(새 트리) 금지 — 저장 안 되고 GC 때 QGIS 가 죽는다
        for ch in list(rg.children()):
            rg.removeChildNode(ch)
        for lyr in lyrs:
            rg.addLayer(lyr)
        if mp is not None:
            lg.model().setLayerStyleOverrides(mp.layerStyleOverrides())
        for n in rg.findLayers():
            lg.model().refreshLayerLegend(n)
            QgsLegendRenderer.setNodeLegendStyle(n, QgsLegendStyle.Hidden)
        lg.refresh()
        print('  범례 다시 채움: %d개 레이어' % len(lyrs))


def safe(name):
    return re.sub(r'[\\/:*?"<>|]+', '_', name).strip() or 'layout'


def main():
    ap = argparse.ArgumentParser(description='헤드리스 조판 내보내기(저장 안 함)')
    ap.add_argument('qgz', help='QGIS 프로젝트(.qgz/.qgs)')
    ap.add_argument('--layout', action='append', default=None, help='조판 이름(여러 번)')
    ap.add_argument('--all', action='store_true', help='모든 조판')
    ap.add_argument('--list', action='store_true', help='조판 목록만 보고 끝')
    ap.add_argument('-o', '--outdir', default='.', help='출력 폴더(파일 이름 = 조판 이름)')
    ap.add_argument('--dpi', type=int, default=300)
    ap.add_argument('--formats', default='png,pdf')
    ap.add_argument('--legend-layers', default=None, help='범례를 다시 채울 레이어 이름(; 로 구분)')
    ap.add_argument('--legend-corner', default='none', choices=['br', 'bl', 'tr', 'tl', 'none'],
                    help='범례를 붙일 모서리(기본 none = 손대지 않음)')
    ap.add_argument('--margin', type=float, default=4.0)
    a = ap.parse_args()

    from qgis.core import QgsApplication, QgsProject
    QgsApplication.setPrefixPath(prefix_path(), True)
    app = QgsApplication([], False)
    app.initQgis()
    rc = 0
    try:
        proj = QgsProject.instance()
        print('프로젝트:', proj.read(a.qgz), a.qgz)
        bad = [lyr.name() for lyr in proj.mapLayers().values() if not lyr.isValid()]
        if bad:
            print('깨진 레이어(그 레이어는 빈칸으로 나온다):', bad)
        mgr = proj.layoutManager()
        names = [lay.name() for lay in mgr.printLayouts()]
        if a.list or not (a.layout or a.all):
            for nm in names:
                lay = mgr.layoutByName(nm)
                pg = lay.pageCollection().page(0).pageSize()
                print('  %-30s %d쪽  %.0f×%.0f mm' % (nm, lay.pageCollection().pageCount(), pg.width(), pg.height()))
            return
        targets = names if a.all else a.layout
        os.makedirs(a.outdir, exist_ok=True)
        for nm in targets:
            lay = mgr.layoutByName(nm)
            if lay is None:
                print('조판 없음:', nm)
                rc = 1
                continue
            print('[%s]' % nm)
            if a.legend_layers:
                rebuild_legend(proj, lay, [v.strip() for v in a.legend_layers.split(';') if v.strip()])
            place_legends(lay, a.legend_corner, a.margin)
            lay.refresh()
            if not export(lay, os.path.join(a.outdir, safe(nm)), a.dpi, [v.strip() for v in a.formats.split(',')]):
                rc = 1
    finally:
        app.exitQgis()
    sys.exit(rc)


if __name__ == '__main__':
    main()
