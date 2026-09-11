// Execute the shipped page script with a small DOM/clock/socket fixture.
// No network, browser package, production database or AI key is needed.
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const path = require('path');
let now = 100000;
class Element {
  constructor() {
    this.style = {}; this.value = ''; this.children = []; this.events = {};
    this.scrollHeight = 600; this.scrollTop = 100; this.clientHeight = 500;
  }
  addEventListener(name, fn) { this.events[name] = fn; }
  appendChild(child) { this.children.push(child); }
  scrollIntoView() { this.scrolled = true; }
  insertAdjacentHTML() {}
}
const nodes = new Map();
const node = id => { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); };
const events = {};
const document = {
  hidden: false, querySelector: node, createElement: () => new Element(),
  createTextNode: text => ({text}), addEventListener: (name, fn) => events[name] = fn,
};
const packets = [];
const context = vm.createContext({document, Date: {now: () => now}, URLSearchParams,
  location: {protocol:'http:', host:'localhost', search:''},
  setTimeout() {}, setInterval() {},
  WebSocket: class { constructor() { this.readyState = 1; } send(body) { packets.push(JSON.parse(body)); } },
});
const html = fs.readFileSync(path.join(__dirname, '../app/web/static/index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1].replace(/\ninit\(\);\s*$/, '');
vm.runInContext(script, context);
const run = code => vm.runInContext(code, context);
const last = () => packets[packets.length - 1];
run("conv='u:1'; connect(); setReading({world:2, revision:0, mode:'reading', requested:false});");
assert.strictEqual(node('#continuous').checked, false);
assert.strictEqual(last().ready, false);
now += 12000; run('readerPulse()');
assert.strictEqual(last().ready, true);
assert(packets.every(p => p.type === 'reader'), 'reading to the end must not send a game action or next click');

document.hidden = true; events.visibilitychange();
assert.strictEqual(last().ready, false);
document.hidden = false; events.visibilitychange();
assert.strictEqual(last().ready, false, 'returning to a tab must wait again');
now += 12000; run('readerPulse()');
assert.strictEqual(last().ready, true);
node('#input').value = '我还在想'; node('#input').events.input();
assert.strictEqual(last().ready, false);
node('#input').value = ''; node('#input').events.input();
assert.strictEqual(last().ready, false);
now += 12000; run('readerPulse()');
node('#chat').scrollTop = 0; node('#chat').events.scroll();
assert.strictEqual(last().ready, false, 'scrolling back suspends continuous reading');

node('#chat').scrollTop = 100;
run("readingCommand('next'); readingCommand('next');");
const next = packets.filter(p => p.type === 'reading');
assert.strictEqual(next.length, 1, 'double click must not queue two chapters');
assert.deepStrictEqual(next[0], {type:'reading', mode:'next', world:2, revision:0});
run("ws.onmessage({data:JSON.stringify({type:'reading', reading:{world:2,revision:0,mode:'reading',requested:true}})})");
assert.strictEqual(last().ready, true, 'an explicit next click does not wait for the auto-reading timer');
assert.strictEqual(node('#readNext').disabled, true);
document.hidden = true; events.visibilitychange();
assert.strictEqual(last().ready, false, 'even an explicit request pauses on leaving the page');
document.hidden = false;
run("render({id:1, role:'bot', text:'雨水落在檐下。', reading:{world:2,revision:1,mode:'reading',requested:false}})");
assert.strictEqual(node('#readNext').disabled, false);
assert.strictEqual(last().ready, false, 'a new paragraph resets the reading delay');
const count = node('#chat').children.length;
run("render({id:1,role:'bot',text:'雨水落在檐下。'})");
assert.strictEqual(node('#chat').children.length, count, 'reconnect history must not duplicate live paragraphs');
node('#chat').scrollTop = 0;
run("render({id:2,role:'bot',text:'脚步停在门外。'})");
assert.strictEqual(node('#chat').scrollTop, 0, 'a new message must not pull a reader away from earlier text');
run("setReading({world:2,revision:2,mode:'reading',intervention:true})");
assert.strictEqual(node('#readNext').disabled, true);
now += 90000; run('readerPulse()');
assert.strictEqual(last().ready, false, 'leaving a foreground page idle also stops output');
events.pointerdown();
assert.strictEqual(last().ready, false, 'returning to read still allows a fresh reading pause');
console.log('WEB READING TEST PASSED (visibility, typing, scroll, dwell, single click, reconnect, agency)');
