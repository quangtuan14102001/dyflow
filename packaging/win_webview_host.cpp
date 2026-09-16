// Native WebView2 window for the frozen Windows exe.
// Built on GitHub Actions (MSVC). No pythonnet / pywebview.
#define UNICODE
#define _UNICODE
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <wrl.h>
#include <wrl/client.h>
#include <wrl/event.h>
#include <string>

#include "WebView2.h"

using namespace Microsoft::WRL;

static ComPtr<ICoreWebView2Controller> g_controller;
static ComPtr<ICoreWebView2> g_webview;
static std::wstring g_url;
static HWND g_hwnd = nullptr;

static void FitWebView(HWND hwnd) {
  if (!g_controller) {
    return;
  }
  RECT bounds{};
  GetClientRect(hwnd, &bounds);
  g_controller->put_Bounds(bounds);
}

static LRESULT CALLBACK WndProc(HWND hwnd, UINT msg, WPARAM wParam, LPARAM lParam) {
  switch (msg) {
    case WM_GETMINMAXINFO: {
      auto* info = reinterpret_cast<MINMAXINFO*>(lParam);
      info->ptMinTrackSize.x = 1100;
      info->ptMinTrackSize.y = 700;
      return 0;
    }
    case WM_SIZE:
      FitWebView(hwnd);
      return 0;
    case WM_DESTROY:
      if (g_controller) {
        g_controller->Close();
        g_controller.Reset();
      }
      g_webview.Reset();
      PostQuitMessage(0);
      return 0;
    default:
      return DefWindowProcW(hwnd, msg, wParam, lParam);
  }
}

static int Fail(const wchar_t* text) {
  MessageBoxW(nullptr, text, L"VideosCreator", MB_OK | MB_ICONERROR);
  return 2;
}

extern "C" __declspec(dllexport) int __cdecl vc_webview_run(
    const wchar_t* url,
    const wchar_t* title,
    const wchar_t* user_data_folder,
    const wchar_t* icon_path) {
  if (!url || !url[0]) {
    return Fail(L"Thieu dia chi app.");
  }
  g_url = url;
  g_controller.Reset();
  g_webview.Reset();

  HRESULT co = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
  bool did_init = SUCCEEDED(co);
  if (FAILED(co) && co != RPC_E_CHANGED_MODE) {
    return Fail(L"Khong khoi tao duoc COM.");
  }

  SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);

  HINSTANCE inst = GetModuleHandleW(nullptr);
  const wchar_t* cls = L"VideosCreatorWnd";

  WNDCLASSEXW wc{};
  wc.cbSize = sizeof(wc);
  wc.style = CS_HREDRAW | CS_VREDRAW;
  wc.lpfnWndProc = WndProc;
  wc.hInstance = inst;
  wc.hCursor = LoadCursorW(nullptr, IDC_ARROW);
  wc.hbrBackground = reinterpret_cast<HBRUSH>(COLOR_WINDOW + 1);
  wc.lpszClassName = cls;
  if (icon_path && icon_path[0]) {
    wc.hIcon = static_cast<HICON>(LoadImageW(
        nullptr, icon_path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE));
    wc.hIconSm = wc.hIcon;
  }
  if (!wc.hIcon) {
    wc.hIcon = LoadIconW(inst, IDI_APPLICATION);
    wc.hIconSm = wc.hIcon;
  }

  ATOM atom = RegisterClassExW(&wc);
  if (!atom && GetLastError() != ERROR_CLASS_ALREADY_EXISTS) {
    if (did_init) {
      CoUninitialize();
    }
    return Fail(L"Khong dang ky duoc cua so Windows.");
  }

  const wchar_t* win_title = (title && title[0]) ? title : L"VideosCreator";
  HWND hwnd = CreateWindowExW(
      0,
      cls,
      win_title,
      WS_OVERLAPPEDWINDOW,
      CW_USEDEFAULT,
      CW_USEDEFAULT,
      1440,
      900,
      nullptr,
      nullptr,
      inst,
      nullptr);
  if (!hwnd) {
    if (did_init) {
      CoUninitialize();
    }
    return Fail(L"Khong tao duoc cua so desktop.");
  }
  g_hwnd = hwnd;
  ShowWindow(hwnd, SW_SHOWMAXIMIZED);
  UpdateWindow(hwnd);

  HRESULT hr = CreateCoreWebView2EnvironmentWithOptions(
      nullptr,
      user_data_folder && user_data_folder[0] ? user_data_folder : nullptr,
      nullptr,
      Callback<ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler>(
          [hwnd](HRESULT result, ICoreWebView2Environment* env) -> HRESULT {
            if (FAILED(result) || !env) {
              MessageBoxW(
                  hwnd,
                  L"Khong mo duoc Microsoft Edge WebView2.\n"
                  L"Cai Evergreen Runtime roi chay lai:\n"
                  L"https://go.microsoft.com/fwlink/p/?LinkId=2124703",
                  L"VideosCreator",
                  MB_OK | MB_ICONERROR);
              DestroyWindow(hwnd);
              return S_OK;
            }
            env->CreateCoreWebView2Controller(
                hwnd,
                Callback<ICoreWebView2CreateCoreWebView2ControllerCompletedHandler>(
                    [hwnd](HRESULT result, ICoreWebView2Controller* controller) -> HRESULT {
                      if (FAILED(result) || !controller) {
                        MessageBoxW(
                            hwnd,
                            L"Khong tao duoc cua so WebView2.",
                            L"VideosCreator",
                            MB_OK | MB_ICONERROR);
                        DestroyWindow(hwnd);
                        return S_OK;
                      }
                      g_controller = controller;
                      g_controller->get_CoreWebView2(&g_webview);
                      if (!g_webview) {
                        DestroyWindow(hwnd);
                        return S_OK;
                      }
                      ComPtr<ICoreWebView2Settings> settings;
                      if (SUCCEEDED(g_webview->get_Settings(&settings)) && settings) {
                        settings->put_IsScriptEnabled(TRUE);
                        settings->put_AreDefaultScriptDialogsEnabled(TRUE);
                        settings->put_IsWebMessageEnabled(TRUE);
                        settings->put_AreDevToolsEnabled(FALSE);
                        settings->put_IsStatusBarEnabled(FALSE);
                      }
                      FitWebView(hwnd);
                      g_webview->Navigate(g_url.c_str());
                      return S_OK;
                    })
                    .Get());
            return S_OK;
          })
          .Get());

  if (FAILED(hr)) {
    Fail(
        L"Khong tim thay Microsoft Edge WebView2 Runtime.\n"
        L"Cai tai: https://go.microsoft.com/fwlink/p/?LinkId=2124703");
    DestroyWindow(hwnd);
    if (did_init) {
      CoUninitialize();
    }
    return 2;
  }

  MSG msg{};
  while (GetMessageW(&msg, nullptr, 0, 0)) {
    TranslateMessage(&msg);
    DispatchMessageW(&msg);
  }

  g_hwnd = nullptr;
  g_controller.Reset();
  g_webview.Reset();
  if (did_init) {
    CoUninitialize();
  }
  return 0;
}
