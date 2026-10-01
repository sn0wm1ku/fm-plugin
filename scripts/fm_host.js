(async function runFM({start, resume, cancel, prompt, tools, instructions = "", timeout_seconds = 120}) {
  const limit = 256 * 1024;
  const failure = (type, message) => ({ok: false, errors: [{type, message}]});
  const fault = (type, message) => Object.assign(new Error(message), {name: type});
  const object = value => value !== null && typeof value === "object" && !Array.isArray(value);
  const canonical = value => Array.isArray(value) ? value.map(canonical) : object(value)
    ? Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value;
  const serialize = value => {
    const text = JSON.stringify(value);
    if (typeof text !== "string") throw fault("InvalidResponse", "Tool result must be JSON serializable");
    let bytes = 0;
    for (const character of text) {
      const point = character.codePointAt(0);
      bytes += point <= 0x7f ? 1 : point <= 0x7ff ? 2 : point <= 0xffff ? 3 : 4;
      if (bytes > limit) throw fault("ResponseTooLarge", "Tool response exceeds 256 KiB");
    }
    return text;
  };
  const unwrap = value => {
    if (object(value?.structuredContent)) return value.structuredContent;
    if (object(value?.structured_content)) return value.structured_content;
    if (Array.isArray(value?.content)) {
      const blocks = value.content.filter(item => item.type === "text");
      if (blocks.length === 1) {
        try { return JSON.parse(blocks[0].text); } catch { /* Plain tool text stays in its MCP envelope. */ }
      }
    }
    return value;
  };
  let session;
  let completed = false;
  let calls = [];
  let seen = {};
  const wholeDeadline = Date.now() + timeout_seconds * 1000;
  const bounded = async (operation, deadline) => {
    const remaining = Math.min(deadline, wholeDeadline) - Date.now();
    if (remaining <= 0) throw fault("TimeoutError", "FM or tool request deadline expired");
    const controller = typeof AbortController === "function" ? new AbortController() : undefined;
    let timer;
    try {
      return await Promise.race([
        Promise.resolve().then(() => operation(controller?.signal)),
        new Promise((resolve, reject) => {
          timer = setTimeout(() => {
            controller?.abort();
            reject(fault("TimeoutError", "FM or tool request deadline expired"));
          }, remaining);
        }),
      ]);
    } finally {
      clearTimeout(timer);
    }
  };
  const state = raw => {
    const value = unwrap(raw);
    if (!object(value) || !["running", "tool_requests", "completed", "failed"].includes(value.status)
        || typeof value.ok !== "boolean") throw fault("InvalidProtocol", "FM returned an invalid status");
    if (value.session_id !== undefined) {
      if (typeof value.session_id !== "string" || !value.session_id || (session && value.session_id !== session))
        throw fault("InvalidProtocol", "FM session identity changed");
      session = value.session_id;
    }
    if (raw?.isError && value.status !== "failed") throw fault("FMError", "FM reported an MCP error");
    if ((value.status === "running" || value.status === "tool_requests") && (!value.ok || !session))
      throw fault("InvalidProtocol", "An active FM session requires a successful status and session ID");
    return value;
  };
  const invoke = async (tool, request) => {
    try {
      const raw = await bounded(signal => tool.call(request.arguments, signal), request.deadline * 1000);
      const value = unwrap(raw);
      const response = raw?.isError
        ? failure("HostToolError", JSON.stringify(value).slice(0, 2000))
        : object(value) && value.ok === false && Array.isArray(value.errors)
          ? {ok: false, errors: value.errors}
          : {ok: true, result: value};
      serialize({id: request.request_id, ...response});
      return response;
    } catch (error) {
      if (error?.name === "TimeoutError") throw error;
      return failure(error?.name || "HostToolError", String(error?.message || error).slice(0, 2000));
    }
  };
  try {
    if (![start, resume, cancel].every(callback => typeof callback === "function"))
      throw fault("InvalidInput", "start, resume and cancel must be callable");
    if (typeof prompt !== "string" || !prompt.trim() || typeof instructions !== "string"
        || !Number.isInteger(timeout_seconds) || timeout_seconds < 5 || timeout_seconds > 300)
      throw fault("InvalidInput", "Provide text prompt/instructions and an integer timeout from 5 to 300 seconds");
    if (!Array.isArray(tools) || tools.length > 16)
      throw fault("InvalidInput", "tools must be an array of at most 16 registered host tools");
    const names = tools.map(tool => tool?.name);
    if (new Set(names).size !== names.length || tools.some(tool => !object(tool)
        || typeof tool.call !== "function" || typeof tool.name !== "string"
        || !/^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(tool.name)
        || typeof tool.description !== "string" || !tool.description.trim() || !object(tool.inputSchema)))
      throw fault("InvalidInput", "Tools require unique names, descriptions, schemas and actual callable references");
    const definitions = tools.map(({name, description, inputSchema}) => ({name, description, inputSchema}));
    let current = state(await bounded(() => start({prompt, instructions, timeout_seconds, tools: definitions}), wholeDeadline));
    while (current.status === "running" || current.status === "tool_requests") {
      let replies = [];
      if (current.status === "tool_requests") {
        if (!Array.isArray(current.requests) || current.requests.length === 0 || current.requests.length > 16)
          throw fault("InvalidProtocol", "FM returned an invalid tool request list");
        for (const request of current.requests) {
          if (!object(request) || typeof request.request_id !== "string" || !/^[a-f0-9]{32}$/.test(request.request_id)
              || typeof request.name !== "string" || !object(request.arguments)
              || !Number.isFinite(request.deadline))
            throw fault("InvalidProtocol", "FM returned an invalid tool request");
          if (Date.now() >= Math.min(wholeDeadline, request.deadline * 1000))
            throw fault("TimeoutError", "FM or tool request deadline expired");
          const tool = tools.find(candidate => candidate.name === request.name);
          if (!tool) throw fault("UnknownTool", "FM requested an unregistered host tool: " + request.name);
          const identity = serialize(canonical({name: request.name, arguments: request.arguments}));
          const previous = seen[request.request_id];
          if (previous && previous.identity !== identity)
            throw fault("ChangedRequest", "FM reused a request ID with different tool arguments");
          let response;
          try {
            response = previous ? previous.response : await invoke(tool, request);
          } catch (error) {
            calls = [...calls, {name: request.name, status: "failed"}];
            throw error;
          }
          if (!previous) {
            seen = {...seen, [request.request_id]: {identity, response}};
            calls = [...calls, {name: request.name, status: response.ok ? "completed" : "failed"}];
          }
          if (!replies.some(reply => reply.request_id === request.request_id))
            replies = [...replies, {request_id: request.request_id, response}];
        }
      }
      current = state(await bounded(() => resume({session_id: session, replies}), wholeDeadline));
    }
    completed = current.status === "completed" && current.ok && calls.every(call => call.status === "completed");
    return completed
      ? {ok: true, status: "completed", result: current.result, tool_calls: calls}
      : {...failure("FMFailed", "FM or a host tool failed"), ...
          (Array.isArray(current.errors) ? {errors: current.errors} : {}), status: "failed", tool_calls: calls};
  } catch (error) {
    return {...failure(error?.name || "HostBridgeError", String(error?.message || error).slice(0, 2000)),
      status: "failed", tool_calls: calls};
  } finally {
    if (session && !completed && typeof cancel === "function") {
      // Cancellation cannot undo host actions or stop a callback that ignores its AbortSignal.
      let timer;
      try {
        await Promise.race([Promise.resolve().then(() => cancel({session_id: session})),
          new Promise(resolve => { timer = setTimeout(resolve, 1000); })]);
      } catch { /* Preserve the original failure if cleanup also fails. */ }
      finally { clearTimeout(timer); }
    }
  }
})
