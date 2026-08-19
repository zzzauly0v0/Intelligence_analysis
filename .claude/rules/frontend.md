---
description: Frontend architecture and conventions for this Vite + React project
globs: ["frontend/src/**/*.ts", "frontend/src/**/*.tsx", "frontend/src/**/*.css"]
---

# Frontend Architecture

## Stack

- Vite + React + TypeScript
- TanStack Router for route-based navigation
- Tailwind CSS for styling
- React Query for data fetching and cache management
- Generated API client under `frontend/src/client/`

This repo is not using Next.js App Router. The route structure follows the existing Vite + React/TanStack Router layout used in this project.

## Structure

- Pages / route files in `frontend/src/routes/`
- Reusable UI in `frontend/src/components/`
- API client and generated SDK in `frontend/src/client/`
- Small app helpers in `frontend/src/lib/`
- App-specific types in `frontend/src/types/`
- Global styles in `frontend/src/index.css`

## Route / Page conventions

- Route files live under `frontend/src/routes/` and follow TanStack Router naming conventions.
- Keep each route focused on one screen or page.
- Route files should be thin: compose components, load data, and handle navigation; avoid embedding business logic directly.
- Use route-level layout files like `__layout.tsx` and `_layout.tsx` for shared chrome.

```tsx
frontend/src/routes/
├── __root.tsx
├── _layout/
│   └── _layout.tsx
├── login.tsx
├── signup.tsx
├── recover-password.tsx
├── reset-password.tsx
└── users/
    └── index.tsx
```

Rules:
- Prefer clear, route-level responsibility over large monolithic screens.
- Use server-side or route-level composition patterns when data is needed before render; keep UI logic in components.
- Keep route files readable; extract repeated form or table logic into `components/`.

## Components

Components belong under `frontend/src/components/` and should be grouped by feature or domain when appropriate.

```tsx
frontend/src/components/
├── Admin/
├── UserSettings/
├── Items/
├── Common/
├── Sidebar/
├── ui/
└── theme-provider.tsx
```

Rules:
- Small and focused; split when a component exceeds roughly 100–150 lines.
- Prefer reusable, generic UI in `components/ui/`.
- Feature-specific components should live near their domain (e.g. `Admin/`, `Items/`, `UserSettings/`).
- Keep props typed and avoid passing anonymous objects where a clearer types contract is helpful.

## API layer

This project generates API clients from the backend OpenAPI schema. The canonical API surface is in `frontend/src/client/`.

```tsx
frontend/src/client/
├── index.ts
├── schemas.gen.ts
├── sdk.gen.ts
├── types.gen.ts
└── core/
```

Rules:
- Prefer generated client services (`frontend/src/client`) for backend requests.
- Use `frontend/src/lib/` for app-level helpers, adapters, or convenience wrappers around the generated client.
- Keep fetching concerns centralized; do not scatter raw `axios`/`fetch` calls across feature components.
- Map backend schemas to frontend types carefully; field names must match the API contract exactly.

Example:

```ts
import { UsersService } from "@/client";

await UsersService.createUser({
  requestBody: {
    email: values.email,
    password: values.password,
    full_name: values.full_name ?? null,
  },
});
```

## Types

Use types in `frontend/src/types/` for app-specific domain models, UI-state types, and validation helpers.

```tsx
frontend/src/types/
├── user.ts
├── item.ts
└── api.ts
```

Rules:
- Keep backend contract types and view-model types distinct when representations differ.
- Prefer explicit types over `any`.
- For generated response shapes, use `frontend/src/client` types and only wrap them when needed in app-local types.
- Keep naming consistent with backend schema field names (`full_name`, `is_active`, `role`, `is_app_admin`, etc.).

## Client components

- Use `"use client"` only when a component needs browser interactivity (hooks, form validation, state, dialogs, events).
- Prefer client components only at the leaves of the tree; keep route/page composition mostly declarative.
- Do not use `"use client"` in shared, stateless display components unless needed.

## Data fetching and mutation

- Use React Query for async data fetching and mutations.
- Use the generated client services when possible; centralize retry/error handling in hooks or helper wrappers.
- Handle API failures consistently with toasts / user-facing error states.
- Keep queries and mutations close to the feature they serve.

Example:

```tsx
const query = useQuery({
  queryKey: ["users"],
  queryFn: () => UsersService.readUsers(),
});
```

## Forms and validation

- Use `react-hook-form` + `zod` for form validation when a form is more than a simple field set.
- Keep validation schemas close to the form they validate.
- Do not duplicate backend logic in the frontend without a clear reason; validate the same contract but allow UI convenience.
- Field names should match API shape exactly.

Example:

```ts
const formSchema = z.object({
  email: z.string().email(),
  full_name: z.string().optional().or(z.literal("")),
  password: z.string().min(8),
  is_active: z.boolean().default(true),
  role: z.enum(["USER", "ADMIN"]).default("USER"),
});
```

## Styling

- Use Tailwind utility classes as the default styling mechanism.
- Reuse existing `components/ui/*` primitives rather than creating one-off custom styled widgets unless the pattern is truly domain-specific.
- Keep layout and spacing consistent with the rest of the app.
- If a component becomes visually dense, extract a small presentational sub-component instead of inlining complicated markup.

## File-level rules

- Prefer one responsibility per file.
- If a component exceeds a roughly 100-150 line threshold, split it into smaller subcomponents or helper functions.
- Keep imports ordered and avoid circular dependencies.
- Use path aliases such as `@/components`, `@/client`, `@/lib`, `@/types` rather than deep relative imports.

## Page/component creation checklist

Before creating a new frontend page or form, ensure:

1. The route or page matches the current project structure (`src/routes/` for pages).
2. Feature logic is placed in the correct domain folder under `components/`.
3. The request body matches backend schema field names exactly.
4. The API request is sent through the generated SDK or a thin helper in `lib/`.
5. Types are explicit and live in `src/types/` or reuse generated `src/client` types.
6. Validation is implemented with `zod` for non-trivial inputs.
7. The UI remains small, focused, and reusable.

## Example pattern

```
export default function NewUserPage() {
  return (
    <div className="mx-auto max-w-4xl p-6">
      <header className="mb-6">
        <h1 className="text-2xl font-bold">Create User</h1>
      </header>

      <UserForm />
    </div>
  );
}
```

The page is thin; the form and API logic live in separate reusable components and helpers. This keeps the app maintainable and consistent with the rest of the project.
