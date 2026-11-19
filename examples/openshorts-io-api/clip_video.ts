// Clip a video with the hosted openshorts.io MCP endpoint (Node 18+, no dependencies).
//
//   export OPENSHORTS_API_KEY=os_...
//   npx tsx clip_video.ts "https://www.youtube.com/watch?v=..." [9:16|4:5|1:1|16:9]

const ENDPOINT = process.env.OPENSHORTS_MCP_URL ?? "https://openshorts.io/api/mcp";
const KEY = process.env.OPENSHORTS_API_KEY;
let nextId = 1;

async function callTool<T = any>(name: string, args: Record<string, unknown>): Promise<T> {
  const res = await fetch(ENDPOINT, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${KEY}`,
      "Content-Type": "application/json",
      Accept: "application/json, text/event-stream",
    },
    body: JSON.stringify({
      jsonrpc: "2.0",
      id: nextId++,
      method: "tools/call",
      params: { name, arguments: args },
    }),
  });
  const reply = await res.json();
  if (reply.error) throw new Error(reply.error.message);
  const text: string = reply.result.content[0].text;
  if (reply.result.isError) throw new Error(text);
  return JSON.parse(text) as T;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const [url, aspect = "9:16"] = process.argv.slice(2);
  if (!url) throw new Error("usage: clip_video.ts <video url> [aspect]");
  if (!KEY) throw new Error("set OPENSHORTS_API_KEY (https://openshorts.io/settings/apikeys)");

  let job = await callTool("clip_video", { url, aspect_ratio: aspect });
  console.log(`job ${job.job_id} queued: ${job.job_url}`);
  do {
    await sleep(30_000);
    job = await callTool("get_job", { job_id: job.job_id });
    console.log(`  ${job.status} ${job.progress ?? ""}`);
  } while (job.status !== "done" && job.status !== "failed");

  if (job.status === "failed") {
    console.error("failed:", job.failure);
    process.exit(1);
  }
  for (const clip of job.clips) console.log(`${clip.score ?? "-"}  ${clip.title}\n     ${clip.video_url}`);
}

main().catch((e) => {
  console.error(e.message ?? e);
  process.exit(1);
});
