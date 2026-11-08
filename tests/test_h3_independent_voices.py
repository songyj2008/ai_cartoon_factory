import pytest
from generation.pipelines.minimax_h3.contracts import H3GenerationConfig
from generation.pipelines.minimax_h3.asset_binder import H3AssetBinder
from generation.pipelines.minimax_h3.prompt_compiler import H3PromptCompiler
from generation.pipelines.minimax_h3.validator import H3Ref2VAValidator
from services.character_voice import attach_character_voices


@pytest.mark.parametrize('usage,expected', [('narration','画外旁白'),('telephone','电话另一端')])
def test_voice_without_matching_character_picture(tmp_path, usage, expected):
    manifest = [{'id':'scene','type':'image','path':'scene.png'},
        {'id':'voice','type':'audio','path':'voice.wav','role':'李经理',
         'metadata':{'voice_usage':usage,'voice_only':True,'duration_sec':3}}]
    attached = attach_character_voices(manifest, {}, tmp_path)
    config = H3GenerationConfig.from_mapping({'asset_manifest':attached})
    bindings = H3AssetBinder().bind(config)
    result = H3PromptCompiler.compact_reference_sections('原始台词', bindings)
    assert expected in result
    assert '<Audio 1>' in result and '李经理' in result
    assert '不新增台词' in result and '不对口型' in result
    assert result == H3PromptCompiler.compact_reference_sections(result, bindings)


def test_independent_voice_requires_name():
    config = H3GenerationConfig.from_mapping({'asset_manifest':[
        {'id':'scene','type':'image','path':'scene.png'},
        {'id':'voice','type':'audio','path':'voice.wav','metadata':{'voice_usage':'telephone','voice_only':True}}]})
    with pytest.raises(ValueError, match='声音角色名称'):
        H3PromptCompiler.compact_reference_sections('台词', H3AssetBinder().bind(config))
