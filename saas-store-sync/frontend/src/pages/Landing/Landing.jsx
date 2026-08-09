import { useEffect } from 'react';
import Navbar from '../../components/landing/Navbar';
import HeroSection from '../../components/landing/HeroSection';
import IntegrationLogos from '../../components/landing/IntegrationLogos';
import ProblemSection from '../../components/landing/ProblemSection';
import FeatureBentoGrid from '../../components/landing/FeatureBentoGrid';
import WorkflowSection from '../../components/landing/WorkflowSection';
import ProductShowcase from '../../components/landing/ProductShowcase';
import ComparisonSection from '../../components/landing/ComparisonSection';
import TeamPermissionsSection from '../../components/landing/TeamPermissionsSection';
import SecuritySection from '../../components/landing/SecuritySection';
import FAQSection from '../../components/landing/FAQSection';
import FinalCTA from '../../components/landing/FinalCTA';
import Footer from '../../components/landing/Footer';

export default function Landing() {
    useEffect(() => {
        const previous = document.title;
        document.title = 'SellerPilot Hub — Marketplace operations, automated';
        return () => {
            document.title = previous;
        };
    }, []);

    return (
        <div className="spl-page min-h-screen antialiased">
            <Navbar />
            <main>
                <HeroSection />
                <IntegrationLogos />
                <ProblemSection />
                <FeatureBentoGrid />
                <WorkflowSection />
                <ProductShowcase />
                <ComparisonSection />
                <TeamPermissionsSection />
                <SecuritySection />
                <FAQSection />
                <FinalCTA />
            </main>
            <Footer />
        </div>
    );
}
