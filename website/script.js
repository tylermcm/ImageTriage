const platform = navigator.platform.toLowerCase();
const userAgent = navigator.userAgent.toLowerCase();

const isWindows = platform.includes("win") || userAgent.includes("windows");
const isLinux = platform.includes("linux") || userAgent.includes("linux");

const selector = isWindows ? ".platform-windows" : isLinux ? ".platform-linux" : "";

if (selector) {
  document.querySelectorAll(selector).forEach((element) => {
    element.classList.add("platform-match");
  });
}
