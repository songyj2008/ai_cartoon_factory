只输出JSON。只读本次来源prompt和asset_manifest，不推测下一段。
按全部Shot的时间顺序提取结束时必要事实。早先明确、后来未改变的状态可保留；最后镜头未入画不等于离场。坐站、位置、视线与摄影机位分开，不因掷文件推断起身。
返回{"state":{"schema_version":1,"scene_id":null,"characters":[{"role_id":"素材metadata.role_id（缺少时identity_id或asset_id）","facts":{"position":{"value":"明确位置","source_quote":"来源连续原文"}}}]},"events":[]}。
facts仅present/position/posture/facing/gaze/holding；present值bool或null，其余值文字或null。未知值null且引用空。确定事实引用来源原文，须支持这一人物的最终状态，不借用其他人物的描述。值不使用Subject/Picture编号，关系对象写明确人物身份；人物相对位置可在该fact中加relative_to_role_id明确关联来源人物。
所有facts字段都必须是对象，present也不例外：{"present":{"value":true,"source_quote":"来源中明确该人物在场的连续原文"},"position":{"value":null,"source_quote":""}}。禁止返回"present":true、"present":false或"present":null这样的裸值。不能确认在场时省略present，或填{"value":null,"source_quote":""}；不能只因素材列表有人物就确认其在场。输出前逐项核对每个fact均有value和source_quote，且确定值的引用确实出现在来源prompt中。
events只记录影响连接的必要动作/道具，格式为actor_role_ids数组、object文字、phase（not_started/in_progress/completed/unknown）、source_quote。已掷出不等于已落地，已躲开不能再当尚未躲。无依据的阶段标unknown。
仅来源明确场景ID可填写scene_id。不能从对白内容推断实际动作；不能把旧自由状态当结束事实。
