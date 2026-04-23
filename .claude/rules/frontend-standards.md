# Frontend implementation standards

1. All user-facing text must come from frontend i18n resources.
2. README and frontend UI copy must support at least `zh-TW`, `en`, and `ja`.
3. Text color tokens must be centrally managed. Prefer UnoCSS theme tokens over ad-hoc color literals.
4. Responsive layout class selection must come from a composable, not scattered inline breakpoint logic.
5. TypeScript must use centralized interfaces, enums, and shared type modules.
6. Prefer enums over broad primitive unions when the domain is finite.
7. TypeScript functions should include concise JSDoc comments.
8. All source-code comments must be written in English.
