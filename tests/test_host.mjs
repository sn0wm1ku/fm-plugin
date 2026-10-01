import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';

const source = await readFile(new URL('../scripts/fm_host.js', import.meta.url), 'utf8');
const runFM = vm.runInNewContext(source, {setTimeout, clearTimeout, AbortController});
const id = 'a'.repeat(32);
const nextId = 'b'.repeat(32);
const session_id = 'c'.repeat(32);
const deadline = () => Date.now() / 1000 + 10;
const request = (request_id = id, name = 'read_record', args = {id: 'one'}) =>
  ({request_id, name, arguments: args, deadline: deadline()});
const pending = (...requests) => ({ok: true, status: 'tool_requests', session_id, requests});
const running = () => ({ok: true, status: 'running', session_id});
const done = () => ({ok: true, status: 'completed', session_id, result: 'translated', tool_evidence: ['large private data']});
const registered = call => ({name: 'read_record', description: 'Read authorized record',
  inputSchema: {type: 'object', properties: {id: {type: 'string'}}, required: ['id'], additionalProperties: false}, call});
const plain = value => JSON.parse(JSON.stringify(value));

function harness(states, call, extra = {}) {
  const invocations = [];
  const replies = [];
  const cancellations = [];
  let starts = 0;
  const args = {
    prompt: 'Read and translate the selected records',
    tools: [registered(async (input, signal) => {
      invocations.push(plain(input));
      return call(input, signal);
    })],
    start: async input => {
      starts += 1;
      assert.equal(input.tools.length, 1);
      assert.equal(input.tools[0].call, undefined);
      return {structuredContent: states.shift()};
    },
    resume: async input => {
      assert.equal(input.session_id, session_id);
      replies.push(plain(input.replies));
      return {content: [{type: 'text', text: JSON.stringify(states.shift())}]};
    },
    cancel: async input => { cancellations.push(plain(input)); },
    ...extra,
  };
  return {args, invocations, replies, cancellations, starts: () => starts};
}

// One JS invocation services multiple real callback references and polling states.
{
  const h = harness([running(), pending(request(), request(nextId, 'read_record', {id: 'two'})), running(), done()],
    async input => ({structuredContent: {body: 'source ' + input.id}}));
  const result = await runFM(h.args);
  assert.equal(result.ok, true);
  assert.equal(result.result, 'translated');
  assert.equal(h.starts(), 1);
  assert.deepEqual(h.invocations, [{id: 'one'}, {id: 'two'}]);
  assert.deepEqual(h.replies[1].map(reply => reply.response.result), [{body: 'source one'}, {body: 'source two'}]);
  assert.equal(result.tool_evidence, undefined);
  assert.deepEqual(plain(result.tool_calls), [{name: 'read_record', status: 'completed'}, {name: 'read_record', status: 'completed'}]);
  assert.equal(h.cancellations.length, 0);
}

// Repeated IDs replay a cached result, never the host action.
{
  const h = harness([pending(request()), pending(request()), done()], async () => 'actual');
  assert.equal((await runFM(h.args)).ok, true);
  assert.equal(h.invocations.length, 1);
  assert.deepEqual(h.replies[0], h.replies[1]);
}
{
  const h = harness([pending(request()), pending(request(id, 'read_record', {id: 'changed'}))], async () => 'actual');
  const result = await runFM(h.args);
  assert.equal(result.errors[0].type, 'ChangedRequest');
  assert.equal(h.invocations.length, 1);
  assert.equal(h.cancellations.length, 1);
}
{
  const h = harness([pending(request(id, 'unregistered'))], async () => 'must not run');
  assert.equal((await runFM(h.args)).errors[0].type, 'UnknownTool');
  assert.equal(h.invocations.length, 0);
  assert.equal(h.cancellations.length, 1);
}

// MCP errors and thrown callback failures go back as actual error envelopes.
for (const call of [async () => ({isError: true, content: [{type: 'text', text: 'Access denied'}]}),
  async () => { throw new Error('Connection failed'); }]) {
  const h = harness([pending(request()), done()], call);
  const result = await runFM(h.args);
  assert.equal(result.ok, false);
  assert.equal(h.replies[0][0].response.ok, false);
  assert.equal(h.cancellations.length, 1);
}

// Expired requests are rejected before dispatch; timeout bounds an in-flight callback.
{
  const h = harness([pending({...request(), deadline: Date.now() / 1000 - 1})], async () => 'must not run');
  assert.equal((await runFM(h.args)).errors[0].type, 'TimeoutError');
  assert.equal(h.invocations.length, 0);
}
{
  let aborted = false;
  const h = harness([pending({...request(), deadline: Date.now() / 1000 + 0.03})],
    async (args, signal) => new Promise(resolve => {
      signal.addEventListener('abort', () => { aborted = true; resolve('late'); });
    }));
  assert.equal((await runFM(h.args)).errors[0].type, 'TimeoutError');
  assert.equal(aborted, true);
  assert.equal(h.invocations.length, 1);
  assert.equal(h.cancellations.length, 1);
}

// A whole-session deadline also bounds running polls, without a callback deadline.
{
  let clock = 0;
  const timedRun = vm.runInNewContext(source, {setTimeout, clearTimeout, AbortController, Date: {now: () => clock}});
  const h = harness([], async () => 'unused', {timeout_seconds: 5,
    start: async () => { clock = 6000; return running(); }});
  assert.equal((await timedRun(h.args)).errors[0].type, 'TimeoutError');
  assert.equal(h.replies.length, 0);
  assert.equal(h.cancellations.length, 1);
}

// Multibyte size is bounded in bytes, and callback metadata is never registered.
{
  const h = harness([pending(request()), done()], async () => '界'.repeat(90000));
  assert.equal((await runFM(h.args)).ok, false);
  assert.equal(h.replies[0][0].response.errors[0].type, 'ResponseTooLarge');
}
{
  const h = harness([], async () => 'unused');
  h.args.tools = [registered(async () => 'one'), registered(async () => 'two')];
  assert.equal((await runFM(h.args)).errors[0].type, 'InvalidInput');
  assert.equal(h.starts(), 0);
}
{
  const h = harness([], async () => 'unused');
  h.args.resume = undefined;
  assert.equal((await runFM(h.args)).errors[0].type, 'InvalidInput');
}
console.log('Host driver checks passed');
