"""Discoverable authoring contract for administrator tools."""


def authoring_contract():
    return {
        'types':{'allowed_fields':['label','enabled','schema','player_access'],
                 'player_access':{'owner_field':'schema中的字符串字段','read_fields':['玩家可读的顶层字段'],'shared_write':False},
                 'visibility':'private只给归属玩家读取；public给所有玩家读取，但仍仅暴露read_fields。秘密不得放在read_fields。'},
        'interactions':{'allowed_fields':['label','enabled','schema','mode','response','allow_users','effects','condition','workflow','player_enabled','player_description','group_enabled','extension'],
                        'group_enabled':'默认false；明确批准在群里公开使用的交互才设true。群聊Agent只读取公开记录，不读取私聊历史、私有记录或裁决。',
                        'modes':{'manual':'裁决后执行','record':'只记录','fixed':'返回静态response','rule':'执行effects或extension','flow':'启动workflow'},
                        'response':'最多4000字符的固定文本，不执行表达式，不支持模板插值或条件。动态结果用extension处理器返回feedback；其他受众通知用messages。',
                        'schema':'必须为object JSONSchema；服务端校验。不支持$ref/$id/正则。'},
        'effects':{'required':['type_key','id_from','field','operation'],
                   'target':'id_from是输入中的记录ID路径，不是字面记录ID；流程中以input.或answers.开头。只能修改已有记录，不能创建。',
                   'operations':{'set':'value或value_from二选一','increment':'value或value_from二选一','append':'value或value_from二选一','compute':'仅expression','plugin':'expression生成输入对象，同时指定plugin_key与tool'},
                   'example':{'type_key':'counter','id_from':'record_id','field':'value','operation':'increment','value':1},
                   'expression_note':'expression语法见forms。声明式表达式无random、无宿主脚本；随机、动态反馈、复杂判断用Python/Node插件。'},
        'conditions':{'example':{'left':{'path':'amount'},'op':'gte','right':{'value':1}},
                      'operands':[{'path':'输入路径'},{'value':'常量'},{'entity':{'id_from':'输入记录ID路径','type_key':'类型','field':'数据字段'}}],
                      'operators':['eq','ne','gt','gte','lt','lte','in','contains','exists'],
                      'groups':['all:[条件]','any:[条件]','not:条件'],
                      'note':'这是条件语言；不要与forms中的{op,args}表达式混用。'},
        'workflows':{'fields':['label','enabled','schema','start','steps'],
                     'steps':{'effects':['kind','effects','extension','next'],'manual':['kind','schema','locks','effects','extension','next'],'branch':['kind','condition','yes','no'],'end':['kind','result']},
                     'limits':'无循环，最多40步；重复玩家行动用多次rule交互修改持久记录，无需循环流程。'},
        'modules':{'fields':['label','description','revision','enabled','requires','types','interactions','workflows','functions'],
                   'version':'module.put.payload.version必须是最新game.version；成功修改定义后递增。inspect_runtime可重新查询。',
                   'validation':'写入前先validate_module检验完整manifest；校验不更改定义、记录或版本。'},
        'design_guidance':[
            '管理员不必懂schema、插件或权限位；自行完成技术实现，只询问缺失的业务规则。导入说明不等于玩法已实现，按原文逐项说明已执行、仅说明、待资料或不在本次范围。',
            '要求玩家先选择/确认的规则须持久记录待确认内容、明确确认/拒绝入口，并重新校验资源和版本。不得仅靠player_instructions禁止扣款；测试确认前/拒绝后无资源变化、确认后只执行一次。',
            '玩家Agent内置request_gm可提交规则争议到GM待办（_review），始终pending，不受自由文字record模式影响，不执行结算。GM决定保存在工作台；不会自动通知QQ或自动执行待办中描述的修改。',
            '自然语言测试可断言expect_action=none/applied/pending、expect_state_changed、expect_records记录JSON Pointer的精确值、回复不包含内容和字数。检查正常与不应执行的情况，不只检查成功词。',
            '聊天参与Agent不应以records为空推断隐藏状态不存在。公开上下文只放授权字段；服务端extension自己解析隐藏局次，不要求玩家模型读取秘密或提供不可见记录ID。',
            '每个交互player_description说明何时选择、参数如何从原话提取、简略跟进如何解释。schema避免无意义可选参数组合；多个交互语义清晰时无需强行合并。',
            '群聊须显式group_enabled；用simulate_actions的audience=group校验授权，再用simulate_conversation的audience=group测试真实自然语言连续多轮。只运行结构化行动不能宣称聊天已修复。',
            '先确定数据、持久状态和交互，选择最少的实现。没有要求人工裁决时，不要把自动玩法做成manual。',
            '随机或复杂逻辑直接创建并测试插件；不要把脚本塞进response或声明式表达式。Python标准库无需pip依赖。',
            '玩家输入通过rule交互extension交给启用插件；不能只创建管理员工具后就声称玩家可用。',
            'extensions服务端读取授权隐藏字段，返回updates、可选feedback给行动者及messages给其他受众。feedback无需QQ绑定；不能把隐藏字段直接返回。messages需要已绑定私聊。',
            'effects/extension只能更新已有记录。可按需求预建共享public状态记录，公开读字段排除秘密，用game范围extension修改；每人私有状态需按真实参与者预建，不得虚构QQ身份。',
            '用plugin manifest.tests检验正常/错误/结束状态；再用simulate_actions连续试玩结算，玩家聊天问题必须额外simulate_conversation经过真实玩家模型。两个工具指定实际private/group受众，数据库最终回滚，插件仍是真实可信代码。不要测试发真实QQ消息。',
            '结束必须给出实际创建的玩法入口、玩家输入示例，以及剩余接入事项。只有读取、方案或测试失败不能算完成。'
        ],
        'minimal_module_example':{'label':'计数示例','types':{'counter':{'schema':{'type':'object','properties':{'value':{'type':'integer'}},'required':['value'],'additionalProperties':False}}},
            'interactions':{'increase':{'mode':'rule','schema':{'type':'object','properties':{'record_id':{'type':'string','x-reference':{'type_key':'counter'}}},'required':['record_id'],'additionalProperties':False},'effects':[{'type_key':'counter','id_from':'record_id','field':'value','operation':'increment','value':1}]}}}
    }
