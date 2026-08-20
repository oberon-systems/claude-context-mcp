import { Link, NavLink, Route, Routes } from "react-router";

import { Empty } from "./components/Common.js";
import { PlanPage } from "./pages/PlanPage.js";
import { PlansPage } from "./pages/PlansPage.js";
import { ProjectPage } from "./pages/ProjectPage.js";
import { ProjectsPage } from "./pages/ProjectsPage.js";
import { SuggestionPage } from "./pages/SuggestionPage.js";
import { SuggestionsPage } from "./pages/SuggestionsPage.js";

export function App() {
  return (
    <>
      <header>
        <Link to="/" className="brand">
          context
        </Link>
        <nav>
          <NavLink to="/" end>
            Projects
          </NavLink>
          <NavLink to="/plans">Plans</NavLink>
          <NavLink to="/suggestions">Suggestions</NavLink>
        </nav>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<ProjectsPage />} />
          <Route path="/projects/:name" element={<ProjectPage />} />
          <Route path="/plans" element={<PlansPage />} />
          <Route path="/plans/:id" element={<PlanPage />} />
          <Route path="/suggestions" element={<SuggestionsPage />} />
          <Route path="/suggestions/:id" element={<SuggestionPage />} />
          <Route
            path="*"
            element={<Empty>There is no such page here.</Empty>}
          />
        </Routes>
      </main>
    </>
  );
}
