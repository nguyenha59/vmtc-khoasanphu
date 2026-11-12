import { Center, Loader } from "@mantine/core";
import type { ReactElement } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { Shell } from "./components/Shell";
import { useMe } from "./lib/auth";
import { AlertPage, Alerts } from "./pages/Alerts";
import { Appointments } from "./pages/Appointments";
import { Config } from "./pages/Config";
import { FormDemo } from "./pages/FormDemo";
import { FormSchedule } from "./pages/FormSchedule";
import { Login } from "./pages/Login";
import { Noshow } from "./pages/Noshow";
import { Overview } from "./pages/Overview";
import { Profile } from "./pages/Profile";
import { PublicForm } from "./pages/PublicForm";
import { Quality } from "./pages/Quality";

export function App() {
  const loc = useLocation();
  const { data: me, isLoading } = useMe();
  // Form thai phụ: trang công khai (mở từ tin nhắn), không cần đăng nhập.
  if (loc.pathname === "/form") return <PublicForm />;
  if (isLoading) return <Center h="100vh"><Loader color="brand" /></Center>;
  if (!me) return <Login />;
  const gate = (perm: string, el: ReactElement) => (me.permissions.includes(perm) ? el : <Navigate to="/" replace />);
  return (
    <Routes>
      <Route element={<Shell me={me} />}>
        <Route index element={<Overview />} />
        <Route path="canh-bao" element={gate("view_alerts", <Alerts />)} />
        <Route path="canh-bao/:id" element={gate("view_alerts", <AlertPage />)} />
        <Route path="lich-hen" element={gate("upload_his", <Appointments />)} />
        <Route path="lich-gui-form" element={gate("form_schedule", <FormSchedule />)} />
        <Route path="ho-so" element={gate("view_profile", <Profile />)} />
        <Route path="du-doan-bo-hen" element={gate("view_noshow", <Noshow />)} />
        <Route path="chat-luong-ai" element={gate("view_quality", <Quality />)} />
        <Route path="cau-hinh" element={gate("view_config", <Config />)} />
        <Route path="form-demo" element={gate("demo_form", <FormDemo />)} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
