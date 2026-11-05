// Remote API on its own port. Deliberately sends NO CORS headers.
const http = require("http");
const port = Number(process.env.REMOTE_PORT || 4101);
http
  .createServer((req, res) => {
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify({ from: "remote", port }));
  })
  .listen(port, () => console.log(`remote on ${port}`));
