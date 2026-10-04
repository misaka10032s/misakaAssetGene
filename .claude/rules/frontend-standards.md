# Frontend implementation standards

1. All user-facing text must come from frontend i18n resources, and README and frontend UI copy must support at least `zh-TW`, `en`, and `ja`.
2. Text color tokens must be centrally managed. Prefer UnoCSS theme tokens over ad-hoc color literals.
3. Responsive layout class selection must come from a composable, not scattered inline breakpoint logic.
4. TypeScript must use centralized interfaces, enums, and shared type modules, preferring enums over broad primitive unions when the domain is finite.
5. TypeScript functions should include concise JSDoc comments.
6. All source-code comments must be written in English.
