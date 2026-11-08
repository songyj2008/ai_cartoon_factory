from services import project_assets as assets
from ui import project_bible_panel as panel


def test_project_names_independent_and_membership_changes_preserve_them(tmp_path, monkeypatch):
    monkeypatch.setattr(assets, 'PROJECTS_DIR', tmp_path)
    assets.save_project_asset_assignments({'characters': ['person'], 'character_names': {
        'person': {'display_name': '王大力', 'aliases': ['大力']}}}, 'A')
    assets.save_project_asset_assignments({'characters': ['person'], 'character_names': {
        'person': {'display_name': '李经理', 'aliases': []}}}, 'B')
    assets.save_project_asset_assignments({'characters': ['person', 'other']}, 'A')
    assert assets.load_project_asset_assignments('A')['character_names']['person']['display_name'] == '王大力'
    assert assets.load_project_asset_assignments('B')['character_names']['person']['display_name'] == '李经理'
    monkeypatch.setattr(panel, 'load_project_bible', lambda *_: {})
    identity = {'id': 'person', 'display_name': '默认人物', 'aliases': ['原别名']}
    for project, name in [('A', '王大力'), ('B', '李经理')]:
        bible = panel._stable_project_bible('', [identity], [], project)
        role = bible['core_characters'][0]
        assert role['display_name'] == name
        assert role['asset_identity_id'] == 'person'
        assert '原别名' not in role['aliases']
    assert identity['display_name'] == '默认人物'
    assets.save_project_asset_assignments({'characters': []}, 'A')
    assert not assets.load_project_asset_assignments('A')['character_names']


def test_empty_project_name_uses_default(tmp_path, monkeypatch):
    monkeypatch.setattr(assets, 'PROJECTS_DIR', tmp_path)
    monkeypatch.setattr(panel, 'load_project_bible', lambda *_: {})
    assets.save_project_asset_assignments({'characters': ['person']}, 'A')
    bible = panel._stable_project_bible('', [{'id':'person','display_name':'默认人物'}], [], 'A')
    assert bible['core_characters'][0]['display_name'] == '默认人物'

def test_name_form_saves_only_selected_person(tmp_path, monkeypatch):
    monkeypatch.setattr(assets, 'PROJECTS_DIR', tmp_path)
    monkeypatch.setattr(panel, 'get_current_project_name', lambda: 'other')
    monkeypatch.setattr(panel, 'load_asset_index', lambda: {'characters': {'identities': [
        {'id': 'a', 'display_name': '人物甲'}, {'id': 'b', 'display_name': '人物乙'}]}})
    monkeypatch.setattr(assets, 'sync_generation_assets', lambda *_: None)
    assets.save_project_asset_assignments({'characters': ['a', 'b'], 'character_names': {
        'b': {'display_name': '乙经理', 'aliases': ['经理']}}}, 'A')
    status, dropdown, name, aliases = panel.save_project_character_name_form('A', 'a', '甲员工', '员工，甲')
    stored = assets.load_project_asset_assignments('A')['character_names']
    assert stored['a'] == {'display_name': '甲员工', 'aliases': ['员工', '甲']}
    assert stored['b'] == {'display_name': '乙经理', 'aliases': ['经理']}
    assert dropdown['value'] == 'a'
    assert name == '甲员工'
    assert panel.project_character_name_form('Empty')[0]['value'] is None

def test_project_gallery_uses_project_name_without_changing_library(tmp_path, monkeypatch):
    monkeypatch.setattr(assets, 'PROJECTS_DIR', tmp_path)
    identity = {'id': 'person', 'display_name': '钱慧芳'}
    monkeypatch.setattr(panel, 'load_asset_index', lambda: {'characters': {'identities': [identity]}})
    monkeypatch.setattr(panel, 'identity_image_options', lambda *_: [{'image_path': 'reference.png'}])
    monkeypatch.setattr(panel, 'asset_project_owners', lambda: {'characters': {'person': ['A', 'B']}, 'backgrounds': {}})
    assets.save_project_asset_assignments({'characters': ['person'], 'character_names': {
        'person': {'display_name': '苏念', 'aliases': []}}}, 'A')
    assets.save_project_asset_assignments({'characters': ['person']}, 'B')
    assert panel._asset_management_records('A')[0][0]['name'] == '苏念'
    assert panel._asset_management_records('B')[0][0]['name'] == '钱慧芳'
    assert panel.project_character_name_form('A')[0]['choices'] == [('苏念', 'person')]
    assert identity['display_name'] == '钱慧芳'
