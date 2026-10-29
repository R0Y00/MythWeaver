"""Two isolated listeners sharing one application, event loop and OneBot session."""
import os
import socket

import uvicorn
from starlette.responses import JSONResponse


def listeners(config=None):
    values=os.environ if config is None else config
    result={}
    for name,default in (('API_PORT',8001),('ONEBOT_PORT',8080)):
        try:value=int(values.get(name) or default)
        except (ValueError,TypeError):raise ValueError(name+' must be an integer TCP port') from None
        if not 1<=value<=65535:raise ValueError(name+' must be between 1 and 65535')
        result[name]=value
    if result['API_PORT']==result['ONEBOT_PORT']:raise ValueError('API_PORT and ONEBOT_PORT must differ. Example: API_PORT=8001, ONEBOT_PORT=8080.')
    for name in ('API_HOST','ONEBOT_HOST'):
        value=values.get(name) or '127.0.0.1'
        if value not in ('127.0.0.1','0.0.0.0'):raise ValueError(name+' must be 127.0.0.1 or 0.0.0.0')
        result[name]=value
    return result


class SplitPorts:
    def __init__(self,app,web_port,onebot_port):
        self.app,self.web_port,self.onebot_port=app,web_port,onebot_port

    async def __call__(self,scope,receive,send):
        if scope['type']=='lifespan':return await self.app(scope,receive,send)
        port=scope.get('server',('',None))[1]
        path=scope.get('path','')
        if port==self.onebot_port:
            if scope['type']=='websocket' and path=='/channels/onebot/v11/ws':
                return await self.app(scope,receive,send)
            if scope['type']=='http' and path=='/health' and scope['method']=='GET':
                return await JSONResponse({'ok':True,'service':'onebot'})(scope,receive,send)
        elif port==self.web_port and not (scope['type']=='websocket' and path=='/channels/onebot/v11/ws'):
            return await self.app(scope,receive,send)
        if scope['type']=='websocket':return await send({'type':'websocket.close','code':1008})
        return await JSONResponse({'code':'NOT_FOUND','message':'此端口不提供该接口'},status_code=404)(scope,receive,send)


def bind(host,port):
    sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    try:
        sock.bind((host,port));sock.listen(128);sock.setblocking(False)
        return sock
    except BaseException:
        sock.close();raise


def main():
    from .api import create_app
    config=listeners()
    app=create_app()
    sockets=[]
    try:
        sockets.append(bind(config['API_HOST'],config['API_PORT']))
        sockets.append(bind(config['ONEBOT_HOST'],config['ONEBOT_PORT']))
        print(f"Web: {config['API_HOST']}:{config['API_PORT']} | OneBot: {config['ONEBOT_HOST']}:{config['ONEBOT_PORT']}",flush=True)
        server=uvicorn.Server(uvicorn.Config(SplitPorts(app,config['API_PORT'],config['ONEBOT_PORT']),workers=1))
        server.run(sockets=sockets)
        if not server.started:raise SystemExit('Platform startup failed; inspect the log above.')
    finally:
        for sock in sockets:sock.close()


if __name__=='__main__':main()
