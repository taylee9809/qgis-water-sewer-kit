# -*- coding: utf-8 -*-
"""GUI 없이: QGIS 프로젝트 열기 → 조판 빌드 스크립트 실행 → 고해상 PNG/PDF (→ 원하면 백업 후 저장)

QGIS MCP(라이브 QGIS)에서 150 dpi 이상으로 조판을 내보내면 QGIS 가 죽는다. 그래서 같은 빌드 스크립트를
헤드리스 PyQGIS 로 돌려 거기서 고해상으로 내보낸다.

사용 (python-qgis-ltr.bat 로 — 일반 파이썬에는 qgis 모듈이 없다):
  "<QGIS>\\bin\\python-qgis-ltr.bat" run_headless.py <프로젝트.qgz> <build.py> <출력 이름(확장자 없이)>
        [--dpi 300] [--layout 이름] [--args args.json] [--legend-corner br] [--formats png,pdf] [--save]

빌드 스크립트(build.py) 약속:
  - 이 스크립트의 전역으로 exec 된다. 쓸 수 있는 이름: `proj`(QgsProject), `BUILD_ARGS`(--args JSON → dict),
    `HEADLESS = True`.
  - 손본 조판을 전역 `l` 또는 `layout` 에 남긴다(없으면 --layout 이름으로 찾는다).
  - 저장·내보내기는 하지 않는다(여기서 한다).
--save 를 주면 프로젝트를 `<이름>_bak_YYMMDD_HHMM.qgz` 로 백업한 뒤 덮어쓴다. 깨진 레이어가 있으면 저장하지 않는다.
★ QT_QPA_PLATFORM=offscreen 을 쓰지 말 것 — 한글 글꼴이 깨진다.
"""
import argparse
import datetime
import json
import os
import shutil
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')


def prefix_path():
    p = os.environ.get('QGIS_PREFIX_PATH')        # python-qgis-ltr.bat 가 설정한다
    if p:
        return p
    import qgis
    return os.path.abspath(os.path.join(os.path.dirname(qgis.__file__), '..', '..'))


def place_legends(layout, corner, margin):
    """범례 크기를 확정한 뒤 페이지 모서리에 붙인다(크기는 렌더 뒤에 정해지므로 내보내기 직전에)."""
    from qgis.core import QgsLayoutExporter, QgsLayoutItemLegend, QgsLayoutPoint, QgsUnitTypes
    from qgis.PyQt.QtCore import QSize
    lgs = [it for it in layout.items() if isinstance(it, QgsLayoutItemLegend)]
    if corner == 'none' or not lgs:
        return
    # 범례 크기는 한 번 그려 봐야 정해진다 → 저해상도로 메모리에만 1차 렌더(파일 안 씀)
    QgsLayoutExporter(layout).renderPageToImage(0, QSize(), 30)
    page = layout.pageCollection().page(0).pageSize()
    W, H = page.width(), page.height()
    for lg in lgs:
        lg.refresh()
        lg.adjustBoxSize()
        w, h = lg.rect().width(), lg.rect().height()
        if w <= 5:
            continue
        x = margin if corner in ('bl', 'tl') else W - margin - w
        y = margin if corner in ('tl', 'tr') else H - margin - h
        lg.attemptMove(QgsLayoutPoint(x, y, QgsUnitTypes.LayoutMillimeters))


def export(layout, base, dpi, formats):
    from qgis.core import QgsLayoutExporter
    res = {}
    if 'png' in formats:
        es = QgsLayoutExporter.ImageExportSettings()
        es.dpi = dpi
        res['png'] = QgsLayoutExporter(layout).exportToImage(base + '.png', es)
    if 'pdf' in formats:
        ps = QgsLayoutExporter.PdfExportSettings()
        ps.dpi = dpi
        res['pdf'] = QgsLayoutExporter(layout).exportToPdf(base + '.pdf', ps)
    ok = {k: (v == QgsLayoutExporter.Success) for k, v in res.items()}
    print('내보내기:', ', '.join('%s %s' % (k, '성공' if v else '실패(%s)' % res[k]) for k, v in ok.items()))
    return all(ok.values())


def main():
    ap = argparse.ArgumentParser(description='헤드리스 조판 빌드 + 고해상 출력')
    ap.add_argument('qgz', help='QGIS 프로젝트(.qgz/.qgs)')
    ap.add_argument('build', help='조판 빌드 스크립트(.py) — 전역 l/layout 에 조판을 남긴다')
    ap.add_argument('out', help='출력 경로(확장자 없이) — .png/.pdf 가 붙는다')
    ap.add_argument('dpi_pos', nargs='?', type=int, default=None, help=argparse.SUPPRESS)  # 옛 호출: … <out> 300
    ap.add_argument('--dpi', type=int, default=300)
    ap.add_argument('--no-save', action='store_true', help=argparse.SUPPRESS)              # 옛 옵션(이제 기본값)
    ap.add_argument('--layout', default=None, help='빌드가 l/layout 을 안 남길 때 쓸 조판 이름')
    ap.add_argument('--args', default=None, help='빌드 스크립트에 넘길 JSON 파일(BUILD_ARGS)')
    ap.add_argument('--legend-corner', default='br', choices=['br', 'bl', 'tr', 'tl', 'none'],
                    help='범례를 붙일 모서리(기본 오른쪽 아래, none = 손대지 않음)')
    ap.add_argument('--margin', type=float, default=4.0, help='범례 모서리 여백 mm')
    ap.add_argument('--formats', default='png,pdf')
    ap.add_argument('--save', action='store_true', help='빌드 결과를 프로젝트에 저장(백업 후)')
    a = ap.parse_args()
    if a.dpi_pos:
        a.dpi = a.dpi_pos

    from qgis.core import QgsApplication, QgsProject
    QgsApplication.setPrefixPath(prefix_path(), True)
    app = QgsApplication([], False)
    app.initQgis()
    try:
        proj = QgsProject.instance()
        print('프로젝트:', proj.read(a.qgz), a.qgz)
        bad = [lyr.name() for lyr in proj.mapLayers().values() if not lyr.isValid()]
        print('깨진 레이어:', bad or '없음')
        g = {'__name__': '__headless__', '__file__': os.path.abspath(a.build), 'proj': proj,
             'HEADLESS': True, 'BUILD_ARGS': {}}
        if a.args:
            with open(a.args, encoding='utf-8') as f:
                g['BUILD_ARGS'] = json.load(f)
        with open(a.build, encoding='utf-8-sig') as f:
            code = compile(f.read(), a.build, 'exec')
        exec(code, g)
        layout = g.get('l') or g.get('layout')
        if layout is None and a.layout:
            layout = proj.layoutManager().layoutByName(a.layout)
        if layout is None:
            raise SystemExit('빌드 스크립트가 조판을 l/layout 에 남기지 않았고 --layout 도 없다')
        place_legends(layout, a.legend_corner, a.margin)
        layout.refresh()
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        ok = export(layout, a.out, a.dpi, [v.strip() for v in a.formats.split(',')])
        if a.save:
            if bad:
                print('저장 안 함: 깨진 레이어가 있다 — 경로를 고친 뒤 다시')
            else:
                root, ext = os.path.splitext(a.qgz)
                bak = root + datetime.datetime.now().strftime('_bak_%y%m%d_%H%M') + ext
                shutil.copy2(a.qgz, bak)
                print('백업:', bak)
                print('저장:', proj.write())
        else:
            print('프로젝트는 저장하지 않음(--save 로 저장)')
        if not ok:
            sys.exit(1)
    finally:
        app.exitQgis()


if __name__ == '__main__':
    main()
