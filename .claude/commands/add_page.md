---
description: Scaffold a new frontend page for creating an entity, with form and API integration
---

Create a new frontend "create entity" page: $ARGUMENTS

Stack: Vite + TanStack Router (file-based) + TanStack Query + react-hook-form + zod + shadcn/ui + biome.
Existing references to copy from: `frontend/src/components/Items/AddItem.tsx`, `frontend/src/components/Admin/AddUser.tsx`, `frontend/src/routes/_layout/items.tsx`.

1. **API client** (`frontend/src/client/`) — generated, never hand-written:
   - `cd frontend && bun run generate-client` (config: `frontend/openapi-ts.config.ts`)
   - Import from `@/client`: `import { type ItemCreate, ItemsService } from "@/client"`
   - Naming: service `<Entity>sService`, method `create<Entity>({ requestBody: data })`, payload type `<Entity>Create`
   - Never add `src/types/*.ts` or a `src/lib/<entity>.ts` API wrapper — `client/types.gen.ts` + `client/sdk.gen.ts` are the only source of truth
   - `src/client/**`, `src/components/ui/**`, `src/routeTree.gen.ts` are biome-ignored — never format or hand-edit them

2. **Field mapping** — open `backend/app/schemas/<entity>.py` and mirror `<Entity>Create` exactly:
   - Only `*Create` fields are writable here; `*Read` fields (`id`, `created_at`, `updated_at`) never appear on a create page; `*Update`-only fields belong to the edit dialog
   - Use the backend names verbatim — `User` is `full_name`, `is_active`, `role`, `is_app_admin` (not `name` / `active` / `admin`)
   - Enum values must match the backend enum in `backend/app/db/models/<entity>.py` (e.g. `UserRole`) character-for-character
   - If `client/types.gen.ts` disagrees with the backend schema, the checked-in client is stale — regenerate before writing the form (it currently still carries `is_superuser` and `{data, count}` lists, while the backend has `is_app_admin` and `*List` = `{items, total}`)
   - Controls: `str` → `Input`, password → `PasswordInput` (`@/components/ui/password-input`), `bool` → `Checkbox`, enum → `Select`, `int` → `Input type="number"`

3. **Form component** (`frontend/src/components/<Entities>/Add<Entity>.tsx` — PascalCase plural folder, e.g. `Items/`; user-facing admin screens live in `Admin/`):
   - `const formSchema = z.object({...})` mirroring `<Entity>Create`, then `type FormData = z.infer<typeof formSchema>`
   - `useForm<FormData>({ resolver: zodResolver(formSchema), mode: "onBlur", criteriaMode: "all", defaultValues: {...} })` — a default for every field
   - `useMutation({ mutationFn: (data: <Entity>Create) => <Entity>sService.create<Entity>({ requestBody: data }) })`
   - `onSuccess`: `showSuccessToast("<Entity> created successfully")` + `form.reset()` + `navigate({ to: "/<entities>" })`
   - `onError: handleError.bind(showErrorToast)` — `handleError` from `@/utils`, toasts from `useCustomToast` (`@/hooks/useCustomToast`)
   - `onSettled`: `queryClient.invalidateQueries({ queryKey: ["<entities>"] })` — same key the list route uses
   - Fields wrapped in `Form` / `FormField` / `FormItem` / `FormLabel` / `FormControl` / `FormMessage` from `@/components/ui/form`
   - Required labels get `<span className="text-destructive">*</span>`; boolean fields use `<FormItem className="flex items-center gap-3 space-y-0">`
   - Submit with `<LoadingButton type="submit" loading={mutation.isPending}>` (`@/components/ui/loading-button`)
   - Page form (not a dialog): wrap in `Card` / `CardHeader` / `CardContent` instead of `Dialog*`

4. **Route** (`frontend/src/routes/_layout/<entities>.new.tsx` → `/<entities>/new`):
   - Flat dot-notation file name; all app pages sit directly under `routes/_layout/`
   - `export const Route = createFileRoute("/_layout/<entities>/new")({ component: New<Entity>, head: () => ({ meta: [{ title: "New <Entity> - FastAPI Template" }] }) })` — keep the same title suffix as sibling routes
   - Admin-only pages add `beforeLoad` with `UsersService.readUserMe()` + `throw redirect({ to: "/" })`, as in `routes/_layout/admin.tsx`
   - `routeTree.gen.ts` regenerates via the router plugin — never edit it
   - Auth and the app shell come from `routes/_layout.tsx`; don't re-check login or re-add padding

5. **Layout** — `_layout.tsx` already provides `p-6 md:p-8` and `mx-auto max-w-7xl`, so the page starts at `flex flex-col gap-6`:
   - Header: `h1.text-2xl.font-bold.tracking-tight` + `p.text-muted-foreground` one-liner (same as `items.tsx` / `admin.tsx`)
   - Body: `grid grid-cols-1 lg:grid-cols-[2fr_1fr] gap-6` — form in the main column, hints in an `aside`
   - Footer actions inside the form: `Cancel` (`variant="outline"`, disabled while pending) then `LoadingButton`

   ```tsx
   function NewItem() {
     return (
       <div className="flex flex-col gap-6">
         <div>
           <h1 className="text-2xl font-bold tracking-tight">New Item</h1>
           <p className="text-muted-foreground">Create and manage your items</p>
         </div>
         <div className="grid grid-cols-1 lg:grid-cols-[2fr_1fr] gap-6">
           <AddItem />
           <aside className="rounded-xl border bg-muted/20 p-4">
             <h2 className="font-semibold mb-2">Guidelines</h2>
             <ul className="space-y-2 text-sm text-muted-foreground">
               <li>Title is required</li>
               <li>Description is optional</li>
             </ul>
           </aside>
         </div>
       </div>
     )
   }
   ```

6. **Navigation** — make the page reachable:
   - From the list route: `<Button asChild><Link to="/<entities>/new">…</Link></Button>` (`Link` from `@tanstack/react-router`)
   - Or as a sidebar entry: add `{ icon, title, path }` to `baseItems` in `frontend/src/components/Sidebar/AppSidebar.tsx` (admin-only entries are appended behind the current-user role check)

7. **Test** (`frontend/tests/<entity>.spec.ts` — flat, lowercase, e.g. `items.spec.ts`):
   - Playwright: heading + description visible, happy-path submit, one validation error
   - Use the helpers in `tests/utils/` (`random.ts`, `user.ts`, `privateApi.ts`)
   - Run: `cd frontend && bun test`

8. Lint: `cd frontend && bun run lint` (biome — double quotes, no semicolons, 2-space indent)
