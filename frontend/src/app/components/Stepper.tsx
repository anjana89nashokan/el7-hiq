import { useLocation, Link } from "react-router-dom";
import { sttmNav, sttmRelativePath } from "../utils/sttmRoutes";

const steps = [
  { path: "/upload", label: "Upload Identifier File(s)" },
  { path: "/profiling", label: "Profiling Results" },
];

export default function Stepper() {
  const location = useLocation();
  const relativePath = sttmRelativePath(location.pathname);
  const currentIndex = steps.findIndex((s) => relativePath === s.path);

  return (
    <div className="flex w-full">
      {steps.map((step, index) => {
        const isActive = index === currentIndex;
        const isCompleted = index < currentIndex;

        return (
          <div key={index} className="flex items-center w-auto">
            <Link
              to={sttmNav(step.path)}
              className={`relative flex items-center w-full h-9 px-4 transition-all duration-200 ${
                isActive
                  ? "bg-brand-darkblue text-white"
                  : isCompleted
                  ? "bg-brand-darkblue text-white"
                  : "bg-brand-light text-font-dark"
              } ${index === 0 ? "clip-right-chevron rounded-t-md rounded-b-md" : "stepper-chevron pl-5"}`}
              style={{
                zIndex: steps.length - index,
              }}
            >
              <div
                className={`flex items-center justify-center w-5 h-5 border rounded-full text-[10px] mr-2 ${
                  isActive || isCompleted
                    ? "border-white text-white"
                    : "text-font-dark border-font-dark"
                }`}
              >
                {index + 1}
              </div>
              <span className="text-xs font-medium truncate pr-2">
                {step.label}
              </span>
            </Link>
          </div>
        );
      })}
    </div>
  );
}
