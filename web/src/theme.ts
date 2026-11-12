import { createTheme, type MantineColorsTuple } from "@mantine/core";

// Xanh ngọc thương hiệu (primary) – sáng → đậm.
const teal: MantineColorsTuple = [
  "#E7F6F3", "#CFEDE7", "#A3DCD0", "#73CAB8", "#4CBAA3",
  "#2FA48C", "#1A8E78", "#0E7C6B", "#0A6457", "#064B42",
];
// Màu mức cảnh báo – khớp core/levels.py LEVEL_COLORS.
const danger: MantineColorsTuple = [
  "#FEF3F2", "#FEE4E2", "#FECDCA", "#FDA29B", "#F97066",
  "#F04438", "#D92D20", "#B42318", "#912018", "#7A271A",
];
const warn: MantineColorsTuple = [
  "#FFFAEB", "#FEF0C7", "#FEDF89", "#FEC84B", "#FDB022",
  "#F79009", "#DC6803", "#B54708", "#8A5100", "#7A2E0E",
];
const ok: MantineColorsTuple = [
  "#ECFDF3", "#D1FADF", "#A6F4C5", "#6CE9A6", "#32D583",
  "#12B76A", "#039855", "#1E7B34", "#05603A", "#054F31",
];
const ink: MantineColorsTuple = [
  "#F5F7F9", "#EBEFF3", "#E1E7EC", "#C9D3DB", "#9AA9B6",
  "#6B7A88", "#4A5A69", "#334454", "#1F3142", "#102A43",
];

export const theme = createTheme({
  primaryColor: "brand",
  primaryShade: 7,
  colors: { brand: teal, danger, warn, ok, ink },
  fontFamily: "'Be Vietnam Pro', system-ui, -apple-system, 'Segoe UI', sans-serif",
  headings: {
    fontFamily: "'Be Vietnam Pro', system-ui, sans-serif",
    fontWeight: "700",
    sizes: { h1: { fontSize: "1.75rem", lineHeight: "1.25" }, h2: { fontSize: "1.35rem" }, h3: { fontSize: "1.1rem" } },
  },
  defaultRadius: "md",
  radius: { xs: "4px", sm: "6px", md: "10px", lg: "14px", xl: "20px" },
  shadows: {
    xs: "0 1px 2px rgba(16,42,67,.05)",
    sm: "0 1px 3px rgba(16,42,67,.06), 0 1px 2px rgba(16,42,67,.04)",
    md: "0 6px 16px -4px rgba(16,42,67,.10), 0 2px 4px rgba(16,42,67,.04)",
    lg: "0 16px 32px -8px rgba(16,42,67,.16)",
  },
  black: "#102A43",
  components: {
    Paper: { defaultProps: { withBorder: true, shadow: "xs", radius: "lg" } },
    Card: { defaultProps: { withBorder: true, shadow: "xs", radius: "lg" } },
    Button: { defaultProps: { radius: "md" } },
    Badge: { defaultProps: { radius: "sm", variant: "light" } },
    TextInput: { defaultProps: { radius: "md" } },
    PasswordInput: { defaultProps: { radius: "md" } },
  },
});

export type Level = "red" | "yellow" | "green";
export const LEVEL = {
  red: { label: "Đỏ", color: "danger", hex: "#B42318" },
  yellow: { label: "Vàng", color: "warn", hex: "#B54708" },
  green: { label: "Xanh", color: "ok", hex: "#1E7B34" },
} as const;
