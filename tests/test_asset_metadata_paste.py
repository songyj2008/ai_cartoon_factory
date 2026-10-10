import json
from pathlib import Path

import gradio as gr
import pytest
from ui import asset_library_panel as panel

SAMPLE = r'''**显示名**\
&#x20;张明
**别名**\
&#x20;神元液工厂高级技术人员，吴天同桌男同事，高等级神元星海使用者
**中文标签**\
&#x20;张明，年轻男性，高级技术人员，神元液工厂，吴天同事，高等级神元星海，十万亿级神经网络，银灰色高级工装，未来科技
**描述**\
&#x20;张明，神元液智能工厂高级技术人员，吴天的同桌男同事，高等级神元星海使用者，拥有十万亿级神经网络。年轻中国男性，短黑发，气质沉稳专业。穿银灰、深灰色高级技术工装，带蓝色科技线条和员工识别标识，整体明显比普通员工工装更加高级精致。
**适用场景**\
&#x20;神元液智能工厂，员工食堂，同桌吃饭，高级营养餐，高等级神元星海，未来科技剧情
**姿态/用途**\
&#x20;character\_reference / senior\_technician / male\_colleague / high\_level\_shenyuan\_user
'''


def test_parse_user_markdown_example():
    values = panel.fill_asset_metadata_from_text(SAMPLE)
    assert values[0] == '张明'
    assert len(panel._as_list_text(values[1])) == 3
    assert len(panel._as_list_text(values[2])) == 9
    assert values[3].endswith('整体明显比普通员工工装更加高级精致。')
    assert len(panel._as_list_text(values[4])) == 6
    assert values[5] == 'character_reference / senior_technician / male_colleague / high_level_shenyuan_user'
    assert '已填入 6 项' in values[6]


def test_partial_parse_preserves_other_fields_and_multiline_description():
    values = panel.fill_asset_metadata_from_text('显示名：张明\n描述：第一行\n第二行')
    assert values[0] == '张明'
    assert values[1] == gr.update()
    assert values[3] == '第一行\n第二行'
    assert values[5] == gr.update()
    assert panel.fill_asset_metadata_from_text('没有字段标题')[:6] == (gr.update(),) * 6


@pytest.mark.parametrize('value', ['张明', '张明_正面-01', 'front', '背景工厂'])
def test_chinese_ids_allowed(value):
    assert panel._safe_id(value) == value


@pytest.mark.parametrize('value', ['../逃逸', 'a/b', 'a\\b', 'C:文件', 'a*', 'a?', 'a\n文件', 'CON', 'LPT1', 'a.'])
def test_unsafe_ids_rejected(value):
    with pytest.raises(ValueError):
        panel._safe_id(value)


def setup_assets(monkeypatch, tmp_path):
    monkeypatch.setattr(panel, 'BASE_DIR', tmp_path)
    monkeypatch.setattr(panel, 'ASSETS_DIR', tmp_path / 'assets')
    monkeypatch.setattr(panel, 'log', lambda *args: None)
    monkeypatch.setattr(panel, 'get_logs', lambda: '')
    monkeypatch.setattr(panel, '_sync_asset_index_for_ui', lambda: None)
    monkeypatch.setattr(panel, 'project_bible_ui_state', lambda: (gr.update(), gr.update(), []))
    source = tmp_path / '张明.png'
    source.write_bytes(b'image-fixture')
    return source


def test_chinese_filename_and_parsed_metadata_save(monkeypatch, tmp_path):
    source = setup_assets(monkeypatch, tmp_path)
    target_dir = tmp_path / 'assets/characters/identities/张明'
    values = panel.fill_asset_metadata_from_text(SAMPLE)
    result = panel.save_selected_asset_metadata(str(source), 'character', '张明_正面', str(target_dir), *values[:6])
    target = target_dir / '张明_正面.png'
    assert Path(result[1]) == target
    assert target.read_bytes() == source.read_bytes()
    meta = json.loads((target_dir / 'asset_meta.json').read_text(encoding='utf-8'))
    assert meta['images']['张明_正面']['display_name'] == '张明'
    assert len(meta['images']['张明_正面']['aliases']) == 3


def test_invalid_id_preview_keeps_source_path(monkeypatch, tmp_path):
    source = setup_assets(monkeypatch, tmp_path)
    result = panel.preview_selected_asset('background', str(source), 'bad/id', '')
    assert result[9] == str(source)
    assert result[10] == str(source)
    corrected = panel.preview_selected_asset_path_only('background', None, '张明', '', result[10])
    assert corrected[10] == str(source)
    assert '张明.png' in corrected[11]
