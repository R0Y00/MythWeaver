import {defineConfig,loadEnv} from 'vite';

export default defineConfig(({mode})=>{
  // The Web API and OneBot use separate listeners. Honor the local Web port.
  const config=loadEnv(mode,'../..','API_');
  return {server:{proxy:{'/api':`http://127.0.0.1:${config.API_PORT||8001}`}}};
});

