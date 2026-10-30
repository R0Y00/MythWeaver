import { Activity, Blocks, BookOpen, Database, FolderKanban, GitBranch, History, LayoutDashboard, MessageSquare, Network, Settings2, ShieldCheck, Sparkles, Users } from 'lucide-react';
// One registry keeps desktop navigation, mobile destinations and search aligned.
export const navigation = [
    { label: '日常工作', items: [
            { key: 'overview', label: '工作概览', description: '待办、游戏状态与下一步', icon: LayoutDashboard },
            { key: 'agent', label: '管理助手', description: '描述目标，让助手落实玩法', icon: Sparkles },
            { key: 'decisions', label: '互动与裁决', description: '查看玩家提交，处理待决定事项', icon: MessageSquare },
            { key: 'data', label: '游戏数据', description: '查询和编辑游戏记录', icon: Database },
        ] },
    { label: '玩法设计', items: [
            { key: 'quality', label: '规则与试玩', description: '核对规则，在隔离空间试玩', icon: BookOpen },
            { key: 'definition', label: '数据与交互', description: '定义字段和玩家行动', icon: Blocks },
            { key: 'flows', label: '流程与决定', description: '自动步骤、分支和人工等待', icon: GitBranch },
            { key: 'control', label: '模块与控制', description: '管理玩法模块与高级操作', icon: ShieldCheck },
            { key: 'blueprints', label: '模板库', description: '保存和复用游戏定义', icon: FolderKanban },
        ] },
    { label: '空间管理', items: [
            { key: 'participants', label: '参与者与权限', description: '管理玩家和参与范围', icon: Users },
            { key: 'history', label: '历史与恢复', description: '审计记录、检查点和恢复', icon: History },
            { key: 'settings', label: '游戏设置', description: '游戏信息、QQ 绑定和接收方式', icon: Settings2 },
        ] },
    { label: '平台', items: [
            { key: 'models', label: '模型管理', description: '配置服务商与模型分工', icon: Network },
            { key: 'platform', label: '平台规则', description: '全局消息规则和执行预算', icon: Settings2 },
            { key: 'operations', label: '连接与诊断', description: '检查 QQ 连接和消息投递', icon: Activity },
        ] },
];
export const destinations = navigation.flatMap(group => group.items);
export const mobileDestinations = destinations.filter(item => ['overview', 'agent', 'decisions', 'quality'].includes(item.key));
export function validPage(value: string | null) { return destinations.some(item => item.key === value) ? value! : 'overview'; }
