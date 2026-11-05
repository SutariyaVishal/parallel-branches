// Host page on one port that fetches from the remote on another origin.
const http = require("http");
const port = Number(process.env.HOST_PORT || 4100);
const remote = process.env.REMOTE_URL || "http://localhost:4101";
const page = `<!doctype html><title>host</title><pre id="out">pending</pre>
<script>
fetch("${remote}/data")
  .then((r) => r.json())
  .then((j) => (document.getElementById("out").textContent = "OK " + JSON.stringify(j)))
  .catch((e) => (document.getElementById("out").textContent = "BLOCKED " + e.message));
</script>`;
http
  .createServer((req, res) => {
    res.setHeader("Content-Type", "text/html");
    res.end(page);
  })
  .listen(port, () => console.log(`host on ${port}`));
