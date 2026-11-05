const http = require("http");
const port = Number(process.env.PORT || 3000);
http
  .createServer((req, res) => res.end(`node-http on ${port}\n`))
  .listen(port, () => console.log(`listening on ${port}`));
