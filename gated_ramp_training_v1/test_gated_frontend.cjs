const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync(__dirname+'/gated.html','utf8');
class El{constructor(){this.value='';this.children=[];}append(...x){this.children.push(...x);}replaceChildren(...x){this.children=x;}setAttribute(k,v){this[k]=v;}}
const nodes={};const document={getElementById:id=>nodes[id]??=new El(),createElement:()=>new El(),createElementNS:()=>new El()};
let calls=[];const config={minimum_hz:1000,maximum_hz:100000,step_hz:100,repeat_us:10000,clock_ppm:5,exposure_ms:60,command_gap_ms:120,line_width:3,seed:20261031,train_cases:1024,validation_cases:256,test_cases:512,epochs:10000,batch_size:32,learning_rate:.001,max_minutes:60};
const sandbox={document,console,setTimeout(){},fetch:async(url,options)=>{calls.push({url,options});return{ok:true,json:async()=>url==='/api/state'?{config,run:'test',state:'idle',epoch:0,message:'IDLE',running:false,history:[],preview:[]}: {ok:true}};}};
const context=vm.createContext(sandbox);vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1],context);
(async()=>{await new Promise(setImmediate);assert.equal(calls.length,1);assert.equal(calls[0].url,'/api/state');assert.equal(nodes.stop.disabled,true);
 await vm.runInContext("action('train')",context);assert.equal(calls.at(-1).url,'/api/train');const payload=JSON.parse(calls.at(-1).options.body);assert.equal(payload.repeat_us,10000);assert.equal(payload.clock_ppm,5);
 await vm.runInContext("action('stop')",context);assert.equal(calls.at(-1).url,'/api/stop');console.log('Frontend idle/actions/payload: PASS');
})().catch(e=>{console.error(e);process.exitCode=1;});
