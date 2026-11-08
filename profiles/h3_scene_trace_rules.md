只输出JSON。只读source.snapshot.prompt、roles参考用途表、此前input_world、scene跟踪名单和当前shots；shots列表顺序对应原稿Shot序号；不得推测下一段。
source_quote必须复制source.snapshot.prompt中的一段连续原文，不能拼接两处句子、删除中间文字或加入分号代替原文。输入已转换为稳定人物身份，引用也使用输入中的同一身份文本。已有位置未改变时省略position，不重新引用或改写此前依据；程序会保留input_world中的原状态。
顶层同时返回events数组，保存影响承接的动作或道具结束阶段。格式为actor_role_ids数组、object文字、phase（not_started/in_progress/completed/unknown）、source_quote来源原文。掷出不等于落地，进行中不得冒充完成，无必要事件填[]。
返回 {"relation":"initial或immediate","shots":[{"shot_uid":"输入的精确ID","world_events":[{"role_id":"跟踪角色ID","facts":{"present":{"value":true,"source_quote":"来源连续原文"}}}],"framing_events":[],"visibility_details":[]}]}。
按全部Shot顺序记录开场建立及真实状态变化；首Shot先写明确的开场事实，再写该镜后续变化。没有变化world_events为空。已有事实无变化不重写，不把末态冒充初态。
facts仅present/position/posture；每项必须为value和source_quote对象，包括present。present值bool或null，其余文字或null；未知用null。确定值引用必须逐字出现在source.snapshot.prompt且确实支持该角色的状态。人物值使用姓名或稳定身份，不写Subject/Picture/Audio编号。相对位置可加relative_to_role_id。
选中素材不代表在场；没入画不代表离场。只记录scene.tracked_role_ids的人物；临时路人照常在画面中，不加入世界记录。保持此前未改变的事实及其依据，禁止为了本段构图编造历史站位。
只切特写、反打、转动摄影机、画面边缘虚化、遮挡均不能改变世界位置或present。确实离开场所才写present=false并将该场所position置null；再次进入若没有位置依据，position为null。不得从掷文件推断站起，不因相对位置锚点移动而推断人物跟随。
framing_events和visibility_details可记录有原文依据的入出画、局部、虚化等，格式为role_id、description、source_quote；不得猜精确秒数。入画人物以输入visible_roles为准，不另改名单；它表示整镜曾入画的并集，允许中途进入。画外发声不自动变成在场人物。
默认操作意图即时连续，但遇明确跳时、换场或冲突返回relation="unclear"并说明reason，不能硬套旧状态。场次第一段可返回initial。未知事实不补造。

场景记录只保留人物、位置、基本姿势、是否在场。posture用站着、坐着、斜靠、仰卧、俯卧、侧卧、蹲着、跪着等简短常用姿势；不要记录表情、视线、语气、情绪、身体微倾等表演细节。位置用简短空间关系，相对人物用relative_to_role_id。input_world里的旧详细表演描述不继承；本次按当前原文重新概括基本姿势。
