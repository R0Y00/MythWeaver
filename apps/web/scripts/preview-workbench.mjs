// UI-only preview: all API routes are intercepted; no database, QQ or model calls.
import {createServer} from 'vite';
import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('..',import.meta.url));
const at=new Date().toISOString();
const schema={type:'object',properties:{name:{type:'string',title:'名称'},description:{type:'string',title:'描述'}},required:['name']};
const game={id:'preview',name:'雾港来信',description:'一座被迷雾笼罩的港口，一封没有署名的来信。与玩家一起探索线索，揭开城市的秘密。',version:8,created_at:at,definition:{types:{clue:{label:'线索',schema}},interactions:{investigate:{label:'调查线索',mode:'manual',enabled:true,schema}},workflows:{},modules:{},functions:{}}};
const submissions=[{id:'s1',version:1,interaction:'investigate',actor:'qq:10001',data:{text:'我想查看旧码头仓库门口留下的脚印。'},status:'pending',result:null,created_at:at},{id:'s2',version:1,interaction:'_review',actor:'qq:10002',data:{text:'守夜人提供的信息和之前的笔记不一致，可以再次询问他吗？'},status:'pending',result:null,created_at:at},{id:'s3',version:1,interaction:'investigate',actor:'qq:10003',data:{text:'检查图书馆里那封没有署名的信。'},status:'applied',result:'发现了一枚蓝色蜡封。',created_at:at}];
const snap={game,entities:[{id:'e1',type_key:'clue',data:{name:'蓝色蜡封',description:'信封上的蜡封刻着一枚月牙。'},visibility:'public',version:1,archived:false},{id:'e2',type_key:'clue',data:{name:'旧港口地图',description:'边角有一行褪色的手写字。'},visibility:'private',version:2,archived:false}],submissions,flows:[],events:[],channels:[{group_id:'100000'}],policy:{paused:false,command_prefix:'/mw',free_mode:'manual',allow_users:[],instructions:'',player_agent_enabled:true,player_instructions:'帮助玩家探索雾港。'},policy_version:1};
const session={id:'session-demo',approval_mode:'manual',title:'完善雾港的调查规则',mode:'harness',pinned:true,archived:false,deleted:false,updated_at:at,last_status:'answered',preview:'一起完善玩家调查线索的方式。',run_count:1};
const run={id:'run-demo',game_id:game.id,session_id:session.id,kind:'harness',prompt:'帮我看看目前的调查玩法，下一步应该做什么？',answer:'目前已有 **线索记录** 和 **调查线索** 交互，玩家提交后由你裁决。\n\n建议先完善三个环节：\n\n1. 说明玩家可以调查哪些对象。\n2. 为线索设置公开与私有范围。\n3. 在隔离试玩中检查“补充描述”和“重复调查”。\n\n你可以先告诉我：调查结果应当自动产生，还是始终由你决定？',status:'answered',error_code:null,plan_id:null,created_at:at,duration_ms:3800,tool_trace:[]};
let sessions=[session],logged=true;
const server=await createServer({root,server:{host:'127.0.0.1',port:18122,strictPort:true},plugins:[{name:'workbench-fixtures',configureServer(server){server.middlewares.use(async(req,res,next)=>{
if(req.url?.startsWith('/__frames')){res.setHeader('Content-Type','text/html');res.end('<html><head><meta charset="utf-8"><title>Responsive workbench preview</title><style>body{margin:0;background:#dce4e7;font:14px system-ui}header{padding:15px 24px;background:#172c35;color:white}main{display:flex;gap:25px;padding:25px;align-items:flex-start}iframe{border:1px solid #a8b9c1;border-radius:16px;background:white;box-shadow:0 10px 40px #17333a20}h3{font-size:13px;color:#42606a}</style></head><body><header>MythWeaver · 隔离响应式预览（虚构数据）</header><main><section><h3>手机 · 390 × 844</h3><iframe title="手机预览" width="390" height="844" src="/#overview"></iframe></section><section><h3>平板 · 768 × 1024</h3><iframe title="平板预览" width="768" height="1024" src="/#overview"></iframe></section></main></body></html>');return;}
if(!req.url?.startsWith('/api/'))return next();
res.setHeader('Content-Type','application/json');const path=new URL(req.url,'http://localhost').pathname;let result={};
if(path==='/api/logout'){logged=false;result={ok:true};}
else if(path==='/api/login'){logged=true;result={ok:true};}
else if(!logged){res.statusCode=401;result={message:'请登录'};}
else if(path==='/api/status')result={version:'0.12.0',onebot:{connected:true,bot_id:'10000',last_seen:0,pending_calls:0,generation:1},agent:{configured:true,model:'示例模型',message:''}};
else if(path==='/api/games')result=[game];
else if(path==='/api/games/preview')result=snap;
else if(path.endsWith('/sessions')&&req.method==='POST'){const row={...session,id:'session-'+Date.now(),title:'新会话',run_count:0};sessions=[row,...sessions];result=row;}
else if(path.endsWith('/sessions'))result={items:sessions,has_more:false,total:sessions.length};
else if(path.includes('/sessions/')){const row=sessions.find(item=>path.endsWith('/'+item.id))||session;result={session:row,runs:row.id===session.id?[run]:[],has_more:false,total:row.id===session.id?1:0};}
else if(path.includes('/tasks/'))result={...run,executions:[],step:1};
else if(path.endsWith('/requirements'))result={documents:[]};
else if(path.endsWith('/trials'))result={trials:[]};
else if(path.endsWith('/flows'))result=[];
else if(path.endsWith('/blueprints'))result=[];
else if(path.endsWith('/participants'))result=[];
else if(path.endsWith('/checkpoints'))result=[];
else if(path.endsWith('/plugins'))result=[];
else if(path==='/api/models')result={providers:[],routing:{},version:1,environment_available:false};
else if(path.endsWith('/policy'))result={...snap.policy,version:1};
else if(req.method!=='GET'){res.statusCode=409;result={message:'这是隔离预览，不执行正式操作。'};}
res.end(JSON.stringify(result));
});}}]});
await server.listen();console.log('UI fixture: http://127.0.0.1:18122/ — phone/tablet: /__frames');
for(const signal of ['SIGINT','SIGTERM'])process.on(signal,async()=>{await server.close();process.exit();});
