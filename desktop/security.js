const { pathToFileURL } = require('url')

function isAppURL(value, port) {
  try {
    const u = new URL(value)
    return u.protocol === 'http:' && u.hostname === '127.0.0.1' &&
      u.port === String(port) && !u.username && !u.password
  } catch { return false }
}

function isShellURL(value, filename) {
  return value === pathToFileURL(filename).href
}

function isTrustedSender(event, contents, filename) {
  return Boolean(contents && event.sender === contents && event.senderFrame &&
    event.senderFrame === contents.mainFrame && isShellURL(event.senderFrame.url, filename))
}

function isExternalURL(value) {
  try {
    const u = new URL(value)
    return u.protocol === 'https:' && !u.username && !u.password
  } catch { return false }
}

module.exports = { isAppURL, isShellURL, isTrustedSender, isExternalURL }
